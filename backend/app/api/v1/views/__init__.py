"""Views da API v1, um modulo por dominio.

Reexporta os nomes para que router.py e os testes importem de
app.api.v1.views sem conhecer o modulo interno de cada viewset.
"""

# Reexport direto de app.permissions: tests/test_permissoes.py confere com
# assertIs que estes nomes sao os mesmos objetos de la.
from app.permissions import get_user_group_name, is_gerente_ou_admin
from .catalogo_de_salgados import CategoriaDeSalgadoViewSet, SalgadoViewSet
from .contas import ContaViewSet
from .fechamentos import (
    EncarregadoViewSet,
    FechamentoCaixaViewSet,
    ResponsavelRetiradaViewSet,
)
from .lojas import LojaViewSet
from .notas import NotaFiscalViewSet
from .plano_de_contas import (
    ElementoDeDespesaViewSet,
    GrupoDeDespesaViewSet,
)
from .usuarios import UsuarioViewSet

__all__ = [
    "CategoriaDeSalgadoViewSet",
    "ContaViewSet",
    "ElementoDeDespesaViewSet",
    "EncarregadoViewSet",
    "FechamentoCaixaViewSet",
    "GrupoDeDespesaViewSet",
    "LojaViewSet",
    "NotaFiscalViewSet",
    "ResponsavelRetiradaViewSet",
    "SalgadoViewSet",
    "UsuarioViewSet",
    "get_user_group_name",
    "is_gerente_ou_admin",
]
