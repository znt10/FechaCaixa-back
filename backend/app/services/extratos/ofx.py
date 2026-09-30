"""Leitura do extrato em OFX, o formato que quase todo banco exporta.

Leitor proprio, tolerante, e nao uma biblioteca de OFX: as bibliotecas
validam o arquivo contra a especificacao, e o OFX de banco brasileiro vive
fora dela — SGML sem tag de fechamento, valor com virgula ("-50,00"), fuso
escrito "[-3:BRT]", arquivo em Windows-1252 dizendo ser ASCII. Um leitor
estrito recusaria justamente os arquivos que a gerente mais vai subir.

So le o que a regra de repeticao precisa: data, valor, descricao, o FITID
(guardado so para consulta) e o numero da conta, para recusar o extrato subido
na conta errada.
"""

import html
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from .linhas import ExtratoLido, LinhaDoExtrato, RecusaDeExtrato

CENTAVO = Decimal("0.01")

# Cada transacao termina no proprio fechamento, no comeco da proxima ou no fim
# da lista: no SGML do OFX 1.x o </STMTTRN> e opcional, e ha banco que omite.
_TRANSACAO = re.compile(
    r"<STMTTRN>(.*?)(?=</STMTTRN>|<STMTTRN>|</BANKTRANLIST>|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def decodificar(conteudo):
    """UTF-8 quando for, Windows-1252 quando nao.

    O cabecalho CHARSET nao e confiavel — ja se viu "USASCII" em cima de um
    arquivo com acento em 1252 — entao quem decide e se os bytes decodificam.
    O 1252 nunca falha, e por isso vem por ultimo.
    """
    if isinstance(conteudo, str):
        return conteudo
    try:
        return conteudo.decode("utf-8")
    except UnicodeDecodeError:
        return conteudo.decode("cp1252", errors="replace")


def _campo(bloco, tag):
    """O valor de <TAG>, com ou sem tag de fechamento. Vazio se nao houver."""
    achado = re.search(rf"<{tag}>\s*([^<\r\n]*)", bloco, re.IGNORECASE)
    return html.unescape(achado.group(1)).strip() if achado else ""


def _data(texto):
    """A data de "20261002120000[-3:BRT]": so os oito primeiros digitos.

    A hora e o fuso ficam de fora de proposito: o extrato e por dia, e
    converter fuso levaria a transacao das 22h de Brasilia para o dia seguinte.
    """
    digitos = texto[:8]
    if len(digitos) != 8 or not digitos.isdigit():
        return None
    try:
        return date(int(digitos[:4]), int(digitos[4:6]), int(digitos[6:8]))
    except ValueError:
        return None


def _valor(texto):
    """Decimal de "-1.234,56", "-1234.56" ou "1,234.56". None se nao for numero.

    O separador decimal e o que aparece por ultimo: e o unico jeito de
    distinguir "1.234,56" de "1,234.56" sem saber o banco.
    """
    limpo = texto.replace(" ", "")
    if "," in limpo and "." in limpo:
        if limpo.rfind(",") > limpo.rfind("."):
            limpo = limpo.replace(".", "").replace(",", ".")
        else:
            limpo = limpo.replace(",", "")
    elif "," in limpo:
        limpo = limpo.replace(",", ".")
    try:
        return Decimal(limpo).quantize(CENTAVO)
    except InvalidOperation:
        return None


def _descricao(bloco):
    """MEMO e NAME juntos quando os dois existem e dizem coisas diferentes.

    Cada banco poe o texto util num campo: o Santander no MEMO, outros no
    NAME, alguns repetem nos dois.
    """
    partes = []
    for tag in ("NAME", "MEMO"):
        texto = " ".join(_campo(bloco, tag).split())
        if texto and texto not in partes:
            partes.append(texto)
    return " - ".join(partes) or _campo(bloco, "TRNTYPE") or "Sem descrição"


def ler_ofx(conteudo):
    """O ExtratoLido do arquivo, ou RecusaDeExtrato dizendo por que nao."""
    texto = decodificar(conteudo)

    if not re.search(r"<OFX>", texto, re.IGNORECASE):
        raise RecusaDeExtrato(
            "O arquivo não é um extrato OFX. No internet banking, exporte o "
            "extrato escolhendo o formato OFX (às vezes chamado de Money)."
        )

    if re.search(r"<CCACCTFROM>", texto, re.IGNORECASE) and not re.search(
        r"<BANKACCTFROM>", texto, re.IGNORECASE
    ):
        raise RecusaDeExtrato(
            "Este arquivo é a fatura de um cartão de crédito, não o extrato "
            "da conta. Exporte o extrato da conta corrente."
        )

    extrato = ExtratoLido()

    conta = re.search(
        r"<BANKACCTFROM>(.*?)(?:</BANKACCTFROM>|<BANKTRANLIST>)",
        texto,
        re.IGNORECASE | re.DOTALL,
    )
    if conta:
        extrato.conta_no_arquivo = _campo(conta.group(1), "ACCTID")

    for numero, bloco in enumerate(_TRANSACAO.findall(texto), start=1):
        data = _data(_campo(bloco, "DTPOSTED"))
        valor = _valor(_campo(bloco, "TRNAMT"))
        if data is None or valor is None:
            # Recusa o arquivo inteiro, e nao so a linha: uma linha pulada
            # sem aviso e dinheiro que some do extrato sem ninguem notar.
            raise RecusaDeExtrato(
                f"Não foi possível ler a data ou o valor da transação "
                f"{numero} do arquivo. Exporte o extrato de novo; se o erro "
                f"continuar, o arquivo deste banco precisa de ajuste no sistema."
            )
        extrato.linhas.append(
            LinhaDoExtrato(
                data=data,
                valor=valor,
                descricao=_descricao(bloco)[:255],
                id_do_banco=_campo(bloco, "FITID")[:255],
            )
        )

    lista = re.search(r"<BANKTRANLIST>(.*)", texto, re.IGNORECASE | re.DOTALL)
    if lista:
        extrato.periodo_de = _data(_campo(lista.group(1), "DTSTART"))
        extrato.periodo_ate = _data(_campo(lista.group(1), "DTEND"))

    datas = [linha.data for linha in extrato.linhas]
    if datas:
        extrato.periodo_de = extrato.periodo_de or min(datas)
        extrato.periodo_ate = extrato.periodo_ate or max(datas)

    return extrato
