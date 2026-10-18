"""Extrato bancario: ler o arquivo, gravar sem repetir, classificar.

Os leitores (ofx.py, csv_picpay.py, pluggy.transacoes) entregam
LinhaDoExtrato; importar.py grava o arquivo sem repetir, e api.py o que vem da
API, um adotando a linha do outro; classificar.py da tipo e categoria;
resumo.py soma.
"""

from .api import gravar_linhas_da_api
from .classificar import ClassificacaoInvalida, classificar
from .categorias import garantir_categorias_de_movimento
from .importar import importar_extrato
from .linhas import RecusaDeExtrato
from .resumo import resumir

__all__ = [
    "ClassificacaoInvalida",
    "RecusaDeExtrato",
    "classificar",
    "garantir_categorias_de_movimento",
    "gravar_linhas_da_api",
    "importar_extrato",
    "resumir",
]
