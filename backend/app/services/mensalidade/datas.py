"""Contas de calendario da mensalidade, sem banco e sem relogio."""

from datetime import date

# Todo mes tem o dia 28. Um vencimento no dia 31 cairia numa data que
# fevereiro nao tem, e o "mes seguinte" viraria erro.
ULTIMO_DIA_DE_VENCIMENTO = 28


def dia_de_vencimento_para(data):
    """O dia do mes em que a mensalidade vence, a partir de uma data."""
    return min(data.day, ULTIMO_DIA_DE_VENCIMENTO)


def mes_seguinte(data, dia):
    """O dia `dia` do mes seguinte ao de `data`. `dia` vai de 1 a 28."""
    if data.month == 12:
        return date(data.year + 1, 1, dia)
    return date(data.year, data.month + 1, dia)
