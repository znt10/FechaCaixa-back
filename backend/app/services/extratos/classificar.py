"""Quem da tipo e categoria a uma transacao do extrato.

Unico caminho de escrita da classificacao, e por isso o lugar das duas
garantias que o modelo nao tem como dar sozinho: Pagamento anda so com
`elemento`, Transferencia e Recebimento so com `categoria`; e as duas pontas de
uma transferencia entre lojas ficam presas uma a outra.
"""

from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import Q

from app.models import RegraDeClassificacao, TipoDeMovimento, TransacaoBancaria

from .categorias import categoria_entre_lojas

# Pix cai na hora e TED no mesmo dia, mas o extrato de um banco pode datar a
# saida no dia em que foi agendada e o outro a entrada no dia seguinte.
JANELA_DO_PAR = timedelta(days=1)


class ClassificacaoInvalida(Exception):
    """A combinacao de tipo e categoria nao fecha. A mensagem vai para a tela."""

    def __init__(self, mensagem):
        self.mensagem = mensagem
        super().__init__(mensagem)


def _candidatas_a_par(conta, de, ate):
    """O que ainda pode ser uma ponta de transferencia entre lojas.

    Sem tipo, ou ja marcada como entre lojas por uma regra aprendida mas ainda
    sem a outra ponta — o extrato da Loja B pode chegar uma semana depois do
    da Loja A.
    """
    return list(
        TransacaoBancaria.objects.filter(
            conta=conta,
            par__isnull=True,
            data__gte=de - JANELA_DO_PAR,
            data__lte=ate + JANELA_DO_PAR,
        )
        .filter(Q(tipo__isnull=True) | Q(categoria__entre_lojas=True))
        .order_by("data", "id")
    )


def _pontas_compativeis(saida, entrada):
    return (
        entrada.valor == -saida.valor
        and entrada.conta_bancaria_id != saida.conta_bancaria_id
        and abs(entrada.data - saida.data) <= JANELA_DO_PAR
    )


def parear_transferencias(conta, de, ate):
    """Acha as duas pontas das transferencias entre contas da empresa.

    A Loja A manda 500 para a Loja B pagar o aluguel: no extrato de A ha uma
    saida de 500, no de B uma entrada de 500, no mesmo dia ou no seguinte. As
    duas viram Transferencia > Entre lojas e apontam uma para a outra.

    So pareia quando nao ha duvida — uma saida casando com exatamente uma
    entrada, e vice-versa. Duas entradas de 500 no mesmo dia podem ser a
    transferencia e uma venda de 500; escolher uma seria chutar, e a venda
    sumiria do consolidado. Na duvida, as duas ficam para a gerente.

    Devolve quantos pares foram feitos.
    """
    candidatas = _candidatas_a_par(conta, de, ate)
    saidas = [t for t in candidatas if t.valor < 0]
    entradas_por_valor = defaultdict(list)
    for transacao in candidatas:
        if transacao.valor > 0:
            entradas_por_valor[transacao.valor].append(transacao)

    categoria = None
    pares = 0
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

        categoria = categoria or categoria_entre_lojas(conta)
        with transaction.atomic():
            for ponta, outra in ((saida, entrada), (entrada, saida)):
                ponta.tipo = TipoDeMovimento.TRANSFERENCIA
                ponta.categoria = categoria
                ponta.elemento = None
                ponta.par = outra
            saida.save(update_fields=["tipo", "categoria", "elemento", "par"])
            entrada.save(update_fields=["tipo", "categoria", "elemento", "par"])
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
    transacao.save(update_fields=["tipo", "elemento", "categoria", "par"])

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
            conta_id=transacao.conta_id,
            assinatura=transacao.assinatura,
            tipo__isnull=True,
            par__isnull=True,
            **sentido,
        )
        .exclude(pk=transacao.pk)
        .update(tipo=tipo, elemento=elemento, categoria=categoria)
    )
