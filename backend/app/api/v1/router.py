from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    CategoriaDeSalgadoViewSet,
    ContaViewSet,
    ElementoDeDespesaViewSet,
    EncarregadoViewSet,
    FechamentoCaixaViewSet,
    GrupoDeDespesaViewSet,
    LojaViewSet,
    NotaFiscalViewSet,
    ResponsavelRetiradaViewSet,
    SalgadoViewSet,
    UsuarioViewSet,
)
from .views.acesso import (
    AcessoAoFormularioView,
    EmpresaDoFormularioView,
    SairDoFormularioView,
)
from .views.planilha import PlanilhaDoPeriodoView
from .views.empresa import (
    AparelhosView,
    DesconectarAparelhoView,
    FuncionarioView,
    FuncionariosView,
    MinhaEmpresaView,
    NovoCodigoView,
)

router = DefaultRouter()
router.register(r"contas", ContaViewSet, basename="contas")
router.register(r"lojas", LojaViewSet)
router.register(
    r"fechamentos-caixa", FechamentoCaixaViewSet, basename="fechamentos-caixa"
)
router.register(r"encarregados", EncarregadoViewSet, basename="encarregados")
router.register(
    r"responsaveis-retirada",
    ResponsavelRetiradaViewSet,
    basename="responsaveis-retirada",
)
router.register(r"user", UsuarioViewSet)
router.register(r"notas-fiscais", NotaFiscalViewSet, basename="notas-fiscais")
router.register(
    r"grupos-de-despesa", GrupoDeDespesaViewSet, basename="grupos-de-despesa"
)
router.register(
    r"elementos-de-despesa",
    ElementoDeDespesaViewSet,
    basename="elementos-de-despesa",
)
router.register(
    r"categorias-de-salgado",
    CategoriaDeSalgadoViewSet,
    basename="categorias-de-salgado",
)
router.register(r"salgados", SalgadoViewSet, basename="salgados")

urlpatterns = router.urls + [
    # A dona fecha o mes na planilha: o painel responde na tela, mas o desconto
    # em folha e a conversa com o contador acontecem no Excel.
    path("planilha/", PlanilhaDoPeriodoView.as_view(), name="planilha"),
    path("minha-empresa/", MinhaEmpresaView.as_view(), name="minha-empresa"),
    path(
        "minha-empresa/novo-codigo/",
        NovoCodigoView.as_view(),
        name="minha-empresa-novo-codigo",
    ),
    path("funcionarios/", FuncionariosView.as_view(), name="funcionarios"),
    path("funcionarios/<int:id>/", FuncionarioView.as_view(), name="funcionario"),
    # A tela da empresa nao lista mais aparelhos, mas poder derrubar UM
    # aparelho perdido — sem trocar o codigo e obrigar todas as lojas a
    # digitar de novo — continua sendo a unica saida para um celular
    # extraviado. A rota fica.
    path("aparelhos/", AparelhosView.as_view(), name="aparelhos"),
    path(
        "aparelhos/<uuid:public_id>/desconectar/",
        DesconectarAparelhoView.as_view(),
        name="aparelho-desconectar",
    ),
    path(
        "formulario/acesso/", AcessoAoFormularioView.as_view(), name="formulario-acesso"
    ),
    path(
        "formulario/empresa/",
        EmpresaDoFormularioView.as_view(),
        name="formulario-empresa",
    ),
    path("formulario/sair/", SairDoFormularioView.as_view(), name="formulario-sair"),
]
