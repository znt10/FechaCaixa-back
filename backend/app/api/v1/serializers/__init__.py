"""Serializers da API v1, organizados por dominio.

Todos os nomes continuam importaveis de `app.api.v1.serializers`.
"""

from .acesso import AcessoAoFormularioSerializer, EmpresaDoFormularioSerializer
from .catalogo_de_salgados import CategoriaDeSalgadoSerializer, SalgadoSerializer
from .contas import ContaSerializer
from .fechamentos import (
    ConsumoDoTurnoSerializer,
    ConsumoLidoSerializer,
    DesperdicioDoTurnoSerializer,
    DesperdicioLidoSerializer,
    EncarregadoSerializer,
    FechamentoCaixaCreateSerializer,
    FechamentoCaixaFormularioSerializer,
    FechamentoCaixaSerializer,
    FechamentoCaixaUpdateSerializer,
    LojaPublicaSerializer,
    ResponsavelRetiradaSerializer,
)
from .lojas import LojaSerializer
from .notas import (  # noqa: F401
    ElementoDeDespesaSerializer,
    FornecedorSerializer,
    GrupoDeDespesaSerializer,
    NotaFiscalSerializer,
)
from .usuarios import UsuarioSerializer

__all__ = [
    "AcessoAoFormularioSerializer",
    "CategoriaDeSalgadoSerializer",
    "ContaSerializer",
    "ConsumoDoTurnoSerializer",
    "ConsumoLidoSerializer",
    "DesperdicioDoTurnoSerializer",
    "DesperdicioLidoSerializer",
    "ElementoDeDespesaSerializer",
    "EncarregadoSerializer",
    "EmpresaDoFormularioSerializer",
    "FechamentoCaixaCreateSerializer",
    "FechamentoCaixaFormularioSerializer",
    "FechamentoCaixaSerializer",
    "FechamentoCaixaUpdateSerializer",
    "FornecedorSerializer",
    "GrupoDeDespesaSerializer",
    "LojaPublicaSerializer",
    "LojaSerializer",
    "NotaFiscalSerializer",
    "ResponsavelRetiradaSerializer",
    "SalgadoSerializer",
    "UsuarioSerializer",
]
