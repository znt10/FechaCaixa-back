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
# O maior que cabe no DECIMAL(12,2) do campo. Acima disto e leitura errada (a
# linha digitavel de um boleto, por exemplo), e o MySQL recusaria a gravacao.
VALOR_MAXIMO = Decimal("9999999999.99")
# "1.500", "1,500", "1.234.567": separador seguido de grupos de 3 digitos e
# milhar, porque dinheiro nunca tem tres casas decimais.
SO_MILHAR = re.compile(r"^\d{1,3}([.,]\d{3})+$")


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
    """'129.80', 'R$ 1.234,56', '1,234.56' ou '1.500' -> Decimal; o resto -> None.

    Na duvida, None: o comprovante vai para "conferir". Um valor errado que
    soma como OK e pior do que um que espera alguem olhar.
    """
    if texto is None:
        return None
    limpo = re.sub(r"[^\d,.]", "", str(texto))
    if "," in limpo and "." in limpo:
        # Os dois: o que aparece por ultimo separa os centavos.
        decimal = "," if limpo.rfind(",") > limpo.rfind(".") else "."
        milhar = "." if decimal == "," else ","
        limpo = limpo.replace(milhar, "").replace(decimal, ".")
    elif SO_MILHAR.match(limpo):
        limpo = limpo.replace(".", "").replace(",", "")
    elif limpo.count(",") + limpo.count(".") > 1:
        return None
    else:
        limpo = limpo.replace(",", ".")
    try:
        valor = Decimal(limpo).quantize(CENTAVOS)
    except InvalidOperation:
        return None
    return valor if Decimal("0") < valor <= VALOR_MAXIMO else None


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
