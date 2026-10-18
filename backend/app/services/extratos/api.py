"""O extrato que vem da API do banco (Pluggy), gravado sem repetir.

A mesma transacao chega pela API em varias rodadas, e pela API e pelo OFX, em
qualquer ordem: ela so pode existir uma vez. Quando uma fonte acha a linha que
a outra ja gravou, adota essa linha — completa o que falta e mantem a
classificacao, o par e a descricao que ja existem.

Cada linha guarda as duas identidades, cada uma unica na conta bancaria: o id
da Pluggy em `id_externo`, e a impressao digital do arquivo em `chave` (linha
que nasceu de arquivo) ou em `chave_do_arquivo` (linha que nasceu da API e um
arquivo adotou depois). O lado do arquivo mora em importar.py.
"""

import re
from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from app.models import ImportacaoDeExtrato, TransacaoBancaria

from .importar import ResultadoDaImportacao, classificar_o_que_entrou
from .linhas import RecusaDeExtrato, assinatura_da_descricao, normalizar_descricao

# A `chave` da linha que nasceu da API. E por ela que o arquivo sabe que a
# linha ainda nao tem impressao digital de arquivo.
PREFIXO_DA_CHAVE = "pluggy:"

# O pedaco do CPF que o Nubank deixa a mostra ("•••.745.338-••") tem seis
# digitos; menos que isso, agencia e data caberiam em qualquer documento.
MENOR_TRECHO_DO_DOCUMENTO = 6

# Um numero na descricao, com os separadores que os bancos poem no meio.
_NUMERO = re.compile(r"\d[\d.\-/]*\d")

_CAMPOS_DA_API = ["id_externo", "contraparte_nome", "contraparte_documento", "contraparte_conta"]


def _so_digitos(texto):
    return re.sub(r"\D", "", texto or "")


def mesma_contraparte(descricao, nome, documento):
    """A descricao fala de quem a API diz que esta do outro lado?

    Pelo documento: o OFX mascara o CPF e deixa o meio dele a mostra, e o
    CNPJ vem inteiro; um trecho de seis digitos ou mais dentro do documento
    completo que a API manda ja diz quem e. Pelo nome, com duas palavras ou
    mais e inteiras: "Ana" sozinho, ou dentro de "Juliana", nao diz.
    """
    documento = _so_digitos(documento)
    if documento:
        for numero in _NUMERO.findall(descricao or ""):
            trecho = _so_digitos(numero)
            if len(trecho) >= MENOR_TRECHO_DO_DOCUMENTO and trecho in documento:
                return True
    nome = normalizar_descricao(nome)
    if len(nome.split()) < 2:
        return False
    inteiro = re.compile(rf"(?<!\w){re.escape(nome)}(?!\w)")
    return inteiro.search(normalizar_descricao(descricao)) is not None


def candidatas_por_dia_e_valor(transacoes):
    """As candidatas a adocao agrupadas por (data, valor), em ordem de id."""
    grupos = defaultdict(list)
    for transacao in transacoes.order_by("id"):
        grupos[(transacao.data, transacao.valor)].append(transacao)
    return grupos


def tirar_candidata(candidatas, e_a_mesma):
    """Tira da lista e devolve a que sera adotada, ou None.

    A primeira que `e_a_mesma` reconhece, senao a primeira; entre iguais, a de
    menor id. Tirada da lista para nao ser adotada duas vezes na mesma rodada:
    dois Pix iguais de R$ 10 no mesmo dia entram dois e ficam dois.
    """
    if not candidatas:
        return None
    escolhida = next((c for c in candidatas if e_a_mesma(c)), candidatas[0])
    candidatas.remove(escolhida)
    return escolhida


@transaction.atomic
def gravar_linhas_da_api(conta_bancaria, linhas, conteudo_bruto):
    """Grava o que a API trouxe de novo e adota o que o arquivo ja gravou.

    Linha a linha: o id ja esta na conta bancaria, repetida; uma linha de
    arquivo ainda sem id, do mesmo dia e valor, e adotada (primeiro a da mesma
    contraparte, senao a primeira) e ganha o id e a contraparte, sem mudar
    tipo, categoria, par nem descricao; senao, linha nova.

    A importacao, que guarda o que a API respondeu, so e criada quando ha
    linha nova: a adotada continua na importacao do arquivo dela.
    """
    if not conta_bancaria.ativo:
        raise RecusaDeExtrato(
            "Esta conta bancária está desativada. Ative a conta antes de "
            "buscar o extrato dela."
        )

    ja_gravadas = set(
        TransacaoBancaria.objects.filter(
            conta_bancaria=conta_bancaria,
            id_externo__in=[linha.id_externo for linha in linhas],
        ).values_list("id_externo", flat=True)
    )
    candidatas = candidatas_por_dia_e_valor(
        TransacaoBancaria.objects.filter(
            conta_bancaria=conta_bancaria,
            id_externo__isnull=True,
            data__in={linha.data for linha in linhas},
        )
    )

    novas, adotadas = [], []
    for linha in linhas:
        if linha.id_externo in ja_gravadas:
            continue
        # Ja aqui, e nao so no banco: a mesma transacao duas vezes na resposta
        # (paginas que se cruzam) seria duas linhas novas com o mesmo id.
        ja_gravadas.add(linha.id_externo)
        transacao = tirar_candidata(
            candidatas[(linha.data, linha.valor)],
            lambda candidata, linha=linha: mesma_contraparte(
                candidata.descricao, linha.contraparte_nome, linha.contraparte_documento
            ),
        )
        if transacao is None:
            novas.append(linha)
            continue
        transacao.id_externo = linha.id_externo
        transacao.contraparte_nome = linha.contraparte_nome
        transacao.contraparte_documento = linha.contraparte_documento
        transacao.contraparte_conta = linha.contraparte_conta
        adotadas.append(transacao)
    TransacaoBancaria.objects.bulk_update(adotadas, _CAMPOS_DA_API)

    importacao = None
    if novas:
        importacao = ImportacaoDeExtrato.objects.create(
            conta_bancaria=conta_bancaria,
            nome_do_arquivo=f"Pluggy {timezone.localtime():%d/%m/%Y %H:%M}",
            conteudo_bruto=conteudo_bruto,
            novas=len(novas),
            repetidas=len(linhas) - len(novas),
        )
        conta = conta_bancaria.loja.conta
        TransacaoBancaria.objects.bulk_create(
            [
                TransacaoBancaria(
                    conta=conta,
                    conta_bancaria=conta_bancaria,
                    importacao=importacao,
                    data=linha.data,
                    valor=linha.valor,
                    descricao=linha.descricao,
                    id_do_banco=linha.id_do_banco,
                    chave=f"{PREFIXO_DA_CHAVE}{linha.id_externo}",
                    assinatura=assinatura_da_descricao(linha.descricao),
                    id_externo=linha.id_externo,
                    contraparte_nome=linha.contraparte_nome,
                    contraparte_documento=linha.contraparte_documento,
                    contraparte_conta=linha.contraparte_conta,
                )
                for linha in novas
            ]
        )

    # Relidas do banco: no MySQL o bulk_create nao devolve o id.
    entraram = [linha.id_externo for linha in novas] + [t.id_externo for t in adotadas]
    pareadas, classificadas, vendas = classificar_o_que_entrou(
        conta_bancaria,
        list(
            TransacaoBancaria.objects.filter(
                conta_bancaria=conta_bancaria, id_externo__in=entraram
            )
        ),
    )

    return ResultadoDaImportacao(
        importacao=importacao,
        novas=len(novas),
        repetidas=len(linhas) - len(novas),
        pareadas=pareadas,
        classificadas=classificadas,
        vendas=vendas,
        adotadas=len(adotadas),
    )
