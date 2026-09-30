"""Extrato bancario: ler o arquivo, gravar sem repetir, classificar.

Os leitores (ofx.py; o CSV do PicPay e a API do banco depois) entregam
LinhaDoExtrato; importar.py grava sem repetir; classificar.py da tipo e
categoria; resumo.py soma.
"""

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
    "importar_extrato",
    "resumir",
]
