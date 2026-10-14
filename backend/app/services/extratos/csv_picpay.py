"""O extrato do PicPay em CSV.

O PicPay nao exporta OFX, so PDF e CSV. O CSV vem em UTF-8 com BOM, separado
por virgula, uma linha por movimento, a mais nova primeiro:

    data,hora,tipo,"origem / destino",valor,"forma de pagamento"
    2026-10-12,23:15,"Pix enviado","FULANA DE TAL","−R$ 7,50","Com saldo"

O sinal vem na frente do valor, e o da saida e o "menos" tipografico (U+2212),
nao o hifen. Nao ha numero de conta nem ID por linha, e nenhum dos dois faz
falta: a chave de repeticao do importador e data, valor e descricao.
"""

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation

from .linhas import ExtratoLido, LinhaDoExtrato, RecusaDeExtrato
from .ofx import decodificar

COMECO_DO_CABECALHO = "data,hora,tipo,"
MENOS = ("−", "-")


def e_csv_do_picpay(comeco):
    """Pelo cabecalho, com ou sem BOM (lido como UTF-8 ou como latin-1)."""
    texto = comeco.lstrip("﻿").removeprefix("ï»¿")
    return texto.lower().startswith(COMECO_DO_CABECALHO)


def _valor(texto):
    """Decimal de "−R$ 1.234,56" ou "+R$ 8,00". None se nao for numero."""
    texto = (texto or "").strip()
    negativo = texto.startswith(MENOS)
    numero = texto.lstrip("+" + "".join(MENOS)).replace("R$", "").strip()
    numero = numero.replace(".", "").replace(",", ".")
    try:
        valor = Decimal(numero)
    except InvalidOperation:
        return None
    return -valor if negativo else valor


def ler_csv_picpay(conteudo):
    texto = decodificar(conteudo).lstrip("﻿")
    linhas = []
    # A linha 1 e o cabecalho: a mensagem conta como a pessoa ve o arquivo.
    for numero, registro in enumerate(csv.DictReader(io.StringIO(texto)), start=2):
        valor = _valor(registro.get("valor"))
        try:
            dia = date.fromisoformat((registro.get("data") or "").strip())
        except ValueError:
            dia = None
        if valor is None or dia is None:
            raise RecusaDeExtrato(
                f"Não deu para ler a linha {numero} do extrato do PicPay. "
                "Exporte o CSV de novo pelo app e tente outra vez."
            )
        descricao = " ".join(
            parte for parte in (
                (registro.get("tipo") or "").strip(),
                (registro.get("origem / destino") or "").strip(),
            ) if parte
        )
        linhas.append(LinhaDoExtrato(data=dia, valor=valor, descricao=descricao))

    datas = [linha.data for linha in linhas]
    return ExtratoLido(
        linhas=linhas,
        periodo_de=min(datas) if datas else None,
        periodo_ate=max(datas) if datas else None,
    )
