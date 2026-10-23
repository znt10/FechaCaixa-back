"""Quem da tipo e categoria a uma transacao do extrato.

Unico caminho de escrita da classificacao, e por isso o lugar das duas
garantias que o modelo nao tem como dar sozinho: Pagamento anda so com
`elemento`, Transferencia e Recebimento so com `categoria`; e as duas pontas de
uma transferencia entre lojas ficam presas uma a outra.
"""

import re
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import Q

from app.models import ContaBancaria, RegraDeClassificacao, TipoDeMovimento, TransacaoBancaria

from .categorias import categoria_de_vendas, categoria_entre_lojas
from .linhas import normalizar_descricao

# Pix cai na hora e TED no mesmo dia, mas o extrato de um banco pode datar a
# saida no dia em que foi agendada e o outro a entrada no dia seguinte.
JANELA_DO_PAR = timedelta(days=1)

# Numero de conta curto demais acharia "conta" dentro de qualquer CPF ou
# valor da descricao. Seis digitos ja e mais que qualquer pedaco de data.
MENOR_NUMERO_DE_CONTA = 6

# Um numero na descricao, com os separadores que os bancos poem no meio:
# "106539100-5", "13.004.982-5".
_NUMERO = re.compile(r"\d[\d.\-/]*\d|\d")


class ClassificacaoInvalida(Exception):
    """A combinacao de tipo e categoria nao fecha. A mensagem vai para a tela."""

    def __init__(self, mensagem):
        self.mensagem = mensagem
        super().__init__(mensagem)


def _candidatas_a_par(conta, de, ate):
    """O que ainda pode ser uma ponta de transferencia entre lojas.

    Sem tipo, ja marcada como entre lojas por uma regra aprendida mas ainda
    sem a outra ponta, ou venda automatica — o extrato da Loja B pode chegar
    uma semana depois do da Loja A, e a entrada de A ja tera virado venda.
    """
    return list(
        TransacaoBancaria.objects.filter(
            conta=conta,
            par__isnull=True,
            data__gte=de - JANELA_DO_PAR,
            data__lte=ate + JANELA_DO_PAR,
        )
        .filter(
            Q(tipo__isnull=True) | Q(categoria__entre_lojas=True) | Q(automatica=True)
        )
        .order_by("data", "id")
    )


def _pontas_compativeis(saida, entrada):
    return (
        entrada.valor == -saida.valor
        and entrada.conta_bancaria_id != saida.conta_bancaria_id
        and abs(entrada.data - saida.data) <= JANELA_DO_PAR
    )


def _digitos(texto):
    return "".join(c for c in (texto or "") if c.isdigit()).lstrip("0")


def _contas_da_empresa(conta):
    """O numero de cada conta bancaria da empresa, so digitos."""
    return [
        (conta_bancaria.id, digitos)
        for conta_bancaria in ContaBancaria.objects.filter(loja__conta=conta)
        if len(digitos := _digitos(conta_bancaria.numero)) >= MENOR_NUMERO_DE_CONTA
    ]


def _conta_citada(transacao, contas):
    """A outra conta da empresa que a descricao cita, ou None.

    O Nubank escreve o destino do Pix na descricao — "Jose ... - PICPAY (0380)
    Agencia: 1 Conta: 106539100-5" —, e quando esse numero e de uma conta
    cadastrada da empresa, nao ha duvida: e transferencia entre lojas, mesmo
    que o valor se repita no dia. A Pluggy traz essa conta num campo proprio
    (`contraparte_conta`), que conta como numero citado do mesmo jeito.
    """
    numeros = [_digitos(numero) for numero in _NUMERO.findall(transacao.descricao or "")]
    if contraparte := _digitos(transacao.contraparte_conta):
        numeros.append(contraparte)
    for conta_id, digitos in contas:
        if conta_id == transacao.conta_bancaria_id:
            continue
        if any(digitos in numero for numero in numeros):
            return conta_id
    return None


def _ligar(saida, entrada, categoria):
    with transaction.atomic():
        for ponta, outra in ((saida, entrada), (entrada, saida)):
            ponta.tipo = TipoDeMovimento.TRANSFERENCIA
            ponta.categoria = categoria
            ponta.elemento = None
            ponta.par = outra
            ponta.automatica = False
        campos = ["tipo", "categoria", "elemento", "par", "automatica"]
        saida.save(update_fields=campos)
        entrada.save(update_fields=campos)


def _parear_pela_conta_citada(conta, candidatas, categoria):
    """As transferencias que a descricao confirma, antes de olhar o valor.

    Quando a descricao cita outra conta cadastrada, a outra ponta e a entrada
    (ou saida) de mesmo valor NAQUELA conta, ate um dia de distancia. Se ha
    mais de uma, qualquer escolha da o mesmo resultado — todas sao entre as
    mesmas duas contas —, entao fica a de data mais perto.

    Sem a outra ponta (o extrato dela ainda nao subiu), a transacao ja vira
    entre lojas sozinha; o par fecha quando o outro extrato chegar, porque
    entre lojas sem par continua candidata.

    Devolve os pares feitos e as transacoes usadas.
    """
    contas = _contas_da_empresa(conta)
    if not contas:
        return 0, set()

    usadas = set()
    pares = 0
    for transacao in candidatas:
        if transacao.pk in usadas:
            continue
        outra_conta = _conta_citada(transacao, contas)
        if outra_conta is None:
            continue

        opostas = [
            outra
            for outra in candidatas
            if outra.pk not in usadas
            and outra.conta_bancaria_id == outra_conta
            and outra.valor == -transacao.valor
            and abs(outra.data - transacao.data) <= JANELA_DO_PAR
        ]
        if opostas:
            outra = min(opostas, key=lambda o: (abs(o.data - transacao.data), o.data, o.id))
            saida, entrada = (transacao, outra) if transacao.valor < 0 else (outra, transacao)
            _ligar(saida, entrada, categoria)
            usadas.update({transacao.pk, outra.pk})
            pares += 1
            continue

        # Fora do pareamento por valor tambem: a descricao ja disse qual e a
        # outra conta, e casar com uma terceira so pelo valor seria errado.
        usadas.add(transacao.pk)
        if transacao.categoria_id != categoria.id:
            transacao.tipo = TipoDeMovimento.TRANSFERENCIA
            transacao.categoria = categoria
            transacao.elemento = None
            transacao.automatica = False
            transacao.save(update_fields=["tipo", "categoria", "elemento", "automatica"])

    return pares, usadas


def parear_transferencias(conta, de, ate):
    """Acha as duas pontas das transferencias entre contas da empresa.

    A Loja A manda 500 para a Loja B pagar o aluguel: no extrato de A ha uma
    saida de 500, no de B uma entrada de 500, no mesmo dia ou no seguinte. As
    duas viram Transferencia > Entre lojas e apontam uma para a outra.

    So pareia quando nao ha duvida — uma saida casando com exatamente uma
    entrada, e vice-versa. Duas entradas de 500 no mesmo dia podem ser a
    transferencia e uma venda de 500; escolher uma seria chutar, e a venda
    sumiria do consolidado. Na duvida, as duas ficam para a gerente.

    Antes disso, o que a descricao confirma: ver `_parear_pela_conta_citada`.

    Devolve quantos pares foram feitos.
    """
    categoria = categoria_entre_lojas(conta)
    candidatas = _candidatas_a_par(conta, de, ate)
    pares, usadas = _parear_pela_conta_citada(conta, candidatas, categoria)
    candidatas = [t for t in candidatas if t.pk not in usadas]

    saidas = [t for t in candidatas if t.valor < 0]
    entradas_por_valor = defaultdict(list)
    for transacao in candidatas:
        if transacao.valor > 0:
            entradas_por_valor[transacao.valor].append(transacao)

    for saida in saidas:
        entradas = [
            e for e in entradas_por_valor[-saida.valor] if _pontas_compativeis(saida, e)
        ]
        if len(entradas) != 1:
            continue
        entrada = entradas[0]
        saidas_da_entrada = [s for s in saidas if _pontas_compativeis(s, entrada)]
        if len(saidas_da_entrada) != 1:
            continue

        _ligar(saida, entrada, categoria)
        entradas_por_valor[entrada.valor].remove(entrada)
        pares += 1

    return pares


def aplicar_regras(conta, transacoes):
    """Classifica pelo que a gerente respondeu antes para a mesma descricao.

    Recebe as transacoes recem-importadas; so toca as que continuam sem tipo
    depois do pareamento. Devolve quantas classificou.
    """
    regras = {
        (regra.assinatura, regra.entrada): regra
        for regra in RegraDeClassificacao.objects.filter(conta=conta)
    }
    if not regras:
        return 0

    classificadas = []
    for transacao in transacoes:
        if transacao.tipo is not None or transacao.par_id is not None:
            continue
        regra = regras.get((transacao.assinatura, transacao.entrada))
        if regra is None:
            continue
        transacao.tipo = regra.tipo
        transacao.elemento_id = regra.elemento_id
        transacao.categoria_id = regra.categoria_id
        classificadas.append(transacao)

    TransacaoBancaria.objects.bulk_update(
        classificadas, ["tipo", "elemento", "categoria"]
    )
    return len(classificadas)


# O cofrinho do PicPay: o dinheiro so muda de lugar dentro da mesma conta.
# Resgatar nao e vender — a entrada fica para a gerente, que ensina uma vez.
MOVIMENTOS_INTERNOS = ("DINHEIRO GUARDADO", "DINHEIRO RESGATADO")


def marcar_entradas_como_venda(conta, transacoes):
    """A entrada que sobrou sem tipo vira Recebimento > Vendas, automatica.

    Roda depois do pareamento e das regras, entao so pega o que nenhum dos
    dois explicou. Ficam de fora o cofrinho e a entrada que ainda pode ser uma
    ponta de transferencia (ha uma saida compativel em outra conta da
    empresa): na duvida o pareamento ja deixa para a gerente, e virar venda
    poria a transferencia na venda em silencio. Devolve quantas marcou.
    """
    entradas = [
        t for t in transacoes
        if t.tipo is None and t.par_id is None and t.valor > 0
        and not normalizar_descricao(t.descricao).startswith(MOVIMENTOS_INTERNOS)
    ]
    if not entradas:
        return 0
    categoria = categoria_de_vendas(conta)
    if categoria is None:
        return 0

    datas = [t.data for t in entradas]
    saidas_abertas = [
        t for t in _candidatas_a_par(conta, min(datas), max(datas)) if t.valor < 0
    ]
    marcadas = []
    for entrada in entradas:
        if any(_pontas_compativeis(saida, entrada) for saida in saidas_abertas):
            continue
        entrada.tipo = TipoDeMovimento.RECEBIMENTO
        entrada.categoria = categoria
        entrada.elemento = None
        entrada.automatica = True
        marcadas.append(entrada)

    TransacaoBancaria.objects.bulk_update(
        marcadas, ["tipo", "categoria", "elemento", "automatica"]
    )
    return len(marcadas)


def conferir_classificacao(tipo, elemento, categoria):
    """Recusa a combinacao que o relatorio nao saberia somar."""
    if tipo is None:
        if elemento is not None or categoria is not None:
            raise ClassificacaoInvalida(
                "Escolha o tipo antes da categoria."
            )
        return
    if tipo == TipoDeMovimento.PAGAMENTO:
        if elemento is None:
            raise ClassificacaoInvalida("Escolha de que é este pagamento.")
        if categoria is not None:
            raise ClassificacaoInvalida(
                "Pagamento usa o plano de contas, não uma categoria de "
                "transferência ou recebimento."
            )
        return
    if categoria is None:
        raise ClassificacaoInvalida("Escolha a categoria.")
    if elemento is not None:
        raise ClassificacaoInvalida(
            "Só pagamento usa o plano de contas."
        )
    if categoria.tipo != tipo:
        raise ClassificacaoInvalida(
            f'A categoria "{categoria.nome}" é de '
            f"{categoria.get_tipo_display().lower()}, não de "
            f"{TipoDeMovimento(tipo).label.lower()}."
        )


@transaction.atomic
def classificar(transacao, tipo, elemento=None, categoria=None, aplicar_as_iguais=False):
    """A classificacao feita na tela. Devolve quantas iguais foram junto.

    Ensina a regra da descricao, como a nota ensina o fornecedor: da proxima
    importacao, a mesma descricao ja chega classificada. Limpar a
    classificacao (tipo None) nao apaga a regra — o que ja se sabia continua
    valendo.

    Tirar uma ponta de transferencia do "entre lojas" solta as duas: a outra
    ponta nao pode continuar apontando para uma transacao que agora e outra
    coisa.
    """
    conferir_classificacao(tipo, elemento, categoria)

    continua_par = (
        tipo == TipoDeMovimento.TRANSFERENCIA
        and categoria is not None
        and categoria.entre_lojas
    )
    if transacao.par_id and not continua_par:
        outra = transacao.par
        TransacaoBancaria.objects.filter(pk=transacao.pk).update(par=None)
        outra.par = None
        outra.save(update_fields=["par"])
        transacao.par = None

    transacao.tipo = tipo
    transacao.elemento = elemento
    transacao.categoria = categoria
    transacao.automatica = False
    transacao.save(update_fields=["tipo", "elemento", "categoria", "par", "automatica"])

    if tipo is None or not transacao.assinatura:
        return 0

    RegraDeClassificacao.objects.update_or_create(
        conta_id=transacao.conta_id,
        assinatura=transacao.assinatura,
        entrada=transacao.entrada,
        defaults={"tipo": tipo, "elemento": elemento, "categoria": categoria},
    )

    if not aplicar_as_iguais:
        return 0

    sentido = {"valor__gt": 0} if transacao.entrada else {"valor__lt": 0}
    return (
        TransacaoBancaria.objects.filter(
            # A venda automatica tambem: o primeiro deposito da loterica chega
            # como venda, e corrigir um tem que corrigir os iguais.
            Q(tipo__isnull=True) | Q(automatica=True),
            conta_id=transacao.conta_id,
            assinatura=transacao.assinatura,
            par__isnull=True,
            **sentido,
        )
        .exclude(pk=transacao.pk)
        .update(tipo=tipo, elemento=elemento, categoria=categoria, automatica=False)
    )
