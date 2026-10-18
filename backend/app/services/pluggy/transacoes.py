"""Transacao da Pluggy vista como linha do extrato.

A Pluggy manda o `date` em UTC; o dia que importa e o de Brasilia. Um Pix das
23h30 de Brasilia chega como 02h30 do dia seguinte em UTC e cairia no dia errado.
"""

import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.services.extratos.linhas import LinhaDoExtrato

FUSO = ZoneInfo("America/Sao_Paulo")
SEM_DESCRICAO = "(sem descrição)"


def _so_digitos(valor):
    return re.sub(r"\D", "", str(valor or ""))


def _descricao(texto):
    limpa = " ".join(str(texto or "").replace("|", " - ").split())[:255]
    return limpa or SEM_DESCRICAO


def _contraparte(transacao, valor):
    pagamento = transacao.get("paymentData") or {}
    lado = pagamento.get("receiver" if valor < 0 else "payer") or {}
    documento = (lado.get("documentNumber") or {}).get("value")
    return (
        " ".join(str(lado.get("name") or "").split())[:150],
        _so_digitos(documento),
        _so_digitos(lado.get("accountNumber")),
    )


def linha_da_transacao(transacao):
    """A linha do extrato, ou None se a transacao ainda nao foi lancada."""
    if transacao.get("status") != "POSTED":
        return None
    instante = datetime.fromisoformat(transacao["date"].replace("Z", "+00:00"))
    valor = Decimal(str(transacao["amount"])).quantize(Decimal("0.01"))
    nome, documento, conta = _contraparte(transacao, valor)
    return LinhaDoExtrato(
        data=instante.astimezone(FUSO).date(),
        valor=valor,
        descricao=_descricao(transacao.get("description")),
        id_externo=transacao["id"],
        contraparte_nome=nome,
        contraparte_documento=documento,
        contraparte_conta=conta,
    )
