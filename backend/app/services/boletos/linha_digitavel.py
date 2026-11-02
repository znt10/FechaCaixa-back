"""A linha digitavel do boleto: texto entra, vencimento e valor saem.

E a conferencia exata da leitura. O modelo le a foto e pode trocar um digito;
os verificadores da linha pegam a troca, e uma linha que passa neles da o
vencimento e o valor por conta, sem depender de onde a data esta impressa.

Nao importa Django de proposito, como o leitor da NFe: o teste roda contra as
linhas sem tocar em tabela nenhuma.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

# O fator de vencimento conta dias a partir daqui. Ele chegou a 9999 em
# 21/02/2025 e recomecou em 1000 no dia seguinte: o mesmo fator serve aos dois
# ciclos.
BASE_DO_CICLO_ANTIGO = date(1997, 10, 7)
INICIO_DO_CICLO_NOVO = date(2025, 2, 22)


@dataclass(frozen=True)
class LinhaDigitavel:
    digitos: str  # 47 ou 48 digitos, sem ponto nem espaco
    tipo: str  # "bancario" | "consumo"
    vencimento: date | None
    valor: Decimal | None


def _modulo_10(numero):
    soma, peso = 0, 2
    for digito in reversed(numero):
        produto = int(digito) * peso
        soma += produto // 10 + produto % 10
        peso = 1 if peso == 2 else 2
    return (10 - soma % 10) % 10


def _soma_modulo_11(numero):
    soma, peso = 0, 2
    for digito in reversed(numero):
        soma += int(digito) * peso
        peso = 2 if peso == 9 else peso + 1
    return soma


def _modulo_11_bancario(numero):
    verificador = 11 - _soma_modulo_11(numero) % 11
    return 1 if verificador in (0, 10, 11) else verificador


def _modulo_11_consumo(numero):
    resto = _soma_modulo_11(numero) % 11
    if resto in (0, 1):
        return 0
    if resto == 10:
        return 1
    return 11 - resto


def _em_reais(centavos):
    valor = int(centavos)
    return (Decimal(valor) / 100).quantize(Decimal("0.01")) if valor else None


def _vencimento(fator, hoje):
    if fator == 0:
        return None
    candidatas = [
        BASE_DO_CICLO_ANTIGO + timedelta(days=fator),
        INICIO_DO_CICLO_NOVO + timedelta(days=fator - 1000),
    ]
    return min(candidatas, key=lambda data: abs((data - hoje).days))


def _bancario(d, hoje):
    campos = [(d[0:9], d[9]), (d[10:20], d[20]), (d[21:31], d[31])]
    if any(_modulo_10(campo) != int(verificador) for campo, verificador in campos):
        return None

    # A barra de 44 posicoes, na ordem do codigo de barras: o verificador geral
    # e calculado sobre ela sem a posicao 4, onde ele mesmo fica.
    campo_livre = d[4:9] + d[10:20] + d[21:31]
    sem_verificador = d[0:4] + d[33:37] + d[37:47] + campo_livre
    if _modulo_11_bancario(sem_verificador) != int(d[32]):
        return None

    return LinhaDigitavel(d, "bancario", _vencimento(int(d[33:37]), hoje), _em_reais(d[37:47]))


def _consumo(d):
    # O 3o digito diz o calculo dos verificadores e se o valor e efetivo (6 e
    # 8) ou so referencia (7 e 9).
    identificador = d[2]
    if identificador in "67":
        verificar = _modulo_10
    elif identificador in "89":
        verificar = _modulo_11_consumo
    else:
        return None

    blocos = [(d[12 * i : 12 * i + 11], d[12 * i + 11]) for i in range(4)]
    if any(verificar(bloco) != int(verificador) for bloco, verificador in blocos):
        return None

    barra = "".join(bloco for bloco, _ in blocos)
    valor = _em_reais(barra[4:15]) if identificador in "68" else None
    # Conta de consumo nao traz vencimento confiavel na linha: ele vem da
    # leitura da foto ou de quem digitou.
    return LinhaDigitavel(d, "consumo", None, valor)


def ler_linha(texto, hoje):
    """A linha conferida, ou None quando ela nao e linha ou um verificador falha."""
    digitos = re.sub(r"\D", "", texto or "")
    if len(digitos) == 47:
        return _bancario(digitos, hoje)
    if len(digitos) == 48 and digitos[0] == "8":
        return _consumo(digitos)
    return None
