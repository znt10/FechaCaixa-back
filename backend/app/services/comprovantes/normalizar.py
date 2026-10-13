"""Como o texto lido do comprovante vira dado comparavel.

O modelo devolve o que viu na tela do banco: CNPJ com ou sem pontuacao,
valor as vezes com virgula, nome com acento. Tudo que compara ou soma passa
por aqui antes.
"""

import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

CENTAVOS = Decimal("0.01")


def so_digitos(texto):
    return re.sub(r"\D", "", texto or "")


def normalizar_nome(texto):
    sem_acento = (
        unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    )
    return " ".join(sem_acento.upper().split())


def mesma_conta(agencia_a, conta_a, agencia_b, conta_b):
    """Agencia e conta iguais, ignorando pontuacao e zero a esquerda."""

    def chave(agencia, conta):
        return so_digitos(agencia).lstrip("0"), so_digitos(conta).lstrip("0")

    a = chave(agencia_a, conta_a)
    return bool(a[1]) and a == chave(agencia_b, conta_b)


def valor_decimal(texto):
    """'129.80', 'R$ 1.234,56' ou '15,00' -> Decimal; o resto -> None."""
    if texto is None:
        return None
    limpo = re.sub(r"[^\d,.]", "", str(texto))
    if "," in limpo:
        # Formato brasileiro: ponto separa milhar, virgula separa centavo.
        limpo = limpo.replace(".", "").replace(",", ".")
    try:
        valor = Decimal(limpo).quantize(CENTAVOS)
    except InvalidOperation:
        return None
    return valor if valor > 0 else None


def data_hora(texto):
    """ISO 8601 sem fuso -> datetime no fuso do projeto (America/Sao_Paulo)."""
    if not texto:
        return None
    try:
        momento = datetime.fromisoformat(str(texto))
    except ValueError:
        return None
    if timezone.is_naive(momento):
        momento = timezone.make_aware(momento)
    return momento
