"""O catalogo de salgados: as familias e os itens.

Duas pontas, e a distincao importa. O painel logado administra o cadastro; o
FORMULARIO DA LOJA, que nao tem login, precisa da lista para o seletor de
desperdicio — e so da conta do proprio aparelho. E o mesmo desenho do
EncarregadoViewSet, e nao o do plano de contas: aquele e lido so pelo painel,
e herdar o IsAuthenticated dele daria 401 na loja.
"""

from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from app.models import CategoriaDeSalgado, Salgado
from app.permissions import (
    IsGerenteOrAdministrador,
    TemAcessoAoFormulario,
    get_conta_do_usuario,
)
from app.services.catalogo_de_salgados import garantir_catalogo_de_salgados

from ..serializers import CategoriaDeSalgadoSerializer, SalgadoSerializer


class CatalogoDeSalgadosMixin:
    lookup_field = "public_id"
    # Sem paginacao: sao 42 itens contra um PAGE_SIZE de 50, e a lista cresce.
    # Paginar aqui repetiria o bug da lista de encarregados, onde a pagina 2
    # simplesmente nao existia na tela — e o seletor do formulario e
    # justamente onde isso mais doi.
    pagination_class = None

    def get_permissions(self):
        if self.action == "list":
            if self.request.user.is_authenticated:
                return [IsAuthenticated()]
            return [TemAcessoAoFormulario()]
        return [IsAuthenticated(), IsGerenteOrAdministrador()]

    def conta_do_pedido(self):
        """A conta de quem esta pedindo: o login, ou o aparelho da loja.

        Semeia ao listar tambem, e nao so no cadastro: quem abre a tela antes
        de cadastrar qualquer coisa veria um seletor vazio.
        """
        user = self.request.user
        if user.is_authenticated:
            conta = get_conta_do_usuario(user)
        else:
            conta = getattr(self.request, "conta_do_formulario", None)
        if conta is not None:
            garantir_catalogo_de_salgados(conta)
        return conta


class CategoriaDeSalgadoViewSet(CatalogoDeSalgadosMixin, viewsets.ModelViewSet):
    serializer_class = CategoriaDeSalgadoSerializer

    def get_queryset(self):
        # is_superuser ANTES de get_conta_do_usuario: para o dono da
        # plataforma aquela funcao devolve None, que aqui significaria "nao ve
        # nada" em vez de "ve tudo".
        if self.request.user.is_superuser:
            return CategoriaDeSalgado.objects.all()

        conta = self.conta_do_pedido()
        if conta is None:
            return CategoriaDeSalgado.objects.none()
        return CategoriaDeSalgado.objects.filter(conta=conta)

    def recusar_nome_repetido(self, serializer, conta):
        """Recusa o nome que ja existe nesta empresa, com 400 em vez de 500.

        O modelo tem a restricao (conta, nome) unica, mas a `conta` entra pelo
        perform_create e nao pelo corpo do pedido: o DRF nao tem os dois lados
        do par e a restricao do banco subiria crua.
        """
        nome = serializer.validated_data.get("nome")
        if nome is None or conta is None:
            return

        irmas = CategoriaDeSalgado.objects.filter(conta=conta, nome=nome)
        if serializer.instance is not None:
            irmas = irmas.exclude(pk=serializer.instance.pk)
        if irmas.exists():
            raise ValidationError(
                f'Ja existe uma categoria chamada "{nome}" nesta empresa.'
            )

    def perform_create(self, serializer):
        conta = get_conta_do_usuario(self.request.user)
        if conta is None:
            raise ValidationError(
                "Este login nao esta ligado a nenhuma empresa. Entre pela "
                "empresa para a qual a categoria deve ser criada."
            )
        self.recusar_nome_repetido(serializer, conta)
        serializer.save(conta=conta)

    def perform_update(self, serializer):
        self.recusar_nome_repetido(serializer, serializer.instance.conta)
        serializer.save()


class SalgadoViewSet(CatalogoDeSalgadosMixin, viewsets.ModelViewSet):
    serializer_class = SalgadoSerializer

    def get_queryset(self):
        base = Salgado.objects.select_related("categoria")
        if self.request.user.is_superuser:
            return base.all()

        conta = self.conta_do_pedido()
        if conta is None:
            return base.none()
        return base.filter(categoria__conta=conta)

    def recusar_nome_repetido(self, serializer):
        """Unico DENTRO da categoria: "Coxinha" em grande e em mini e o caso
        real do catalogo, e nao colisao."""
        # Fallback para o instance em nome e categoria: num PATCH parcial que
        # so manda a categoria nova (mover o item de familia sem redigitar o
        # nome), validated_data nao tem "nome".
        #
        # Quem responde 400 hoje NAO e esta funcao: e o UniqueTogetherValidator
        # que o DRF monta sozinho a partir do UniqueConstraint(categoria, nome)
        # do modelo, porque os dois campos estao no serializer — e ele roda
        # dentro do is_valid(), antes do perform_update chamar este metodo.
        # Sem o fallback, este metodo especifico sai cedo e nao acusa nada,
        # mas a resposta continua 400 do mesmo jeito, via aquele validador.
        #
        # Esta guarda existe como REDE, nao como o mecanismo atual: se um dia
        # "categoria" ou "nome" sairem de Meta.fields (ou virarem read_only),
        # o validador automatico do DRF some em silencio, e ai sim esta
        # checagem passa a ser a unica defesa contra o IntegrityError do
        # unique_together no banco. E por isso o fallback de nome existe: sem
        # ele a rede teria um furo justamente no PATCH parcial.
        #
        # Contraste com CategoriaDeSalgadoViewSet.recusar_nome_repetido: la
        # nao ha validador automatico (o campo conta nao esta no serializer,
        # entra so pelo perform_create/update), entao aquela checagem manual
        # e a defesa real, ja em uso, e nao uma rede para o futuro.
        nome = serializer.validated_data.get("nome") or (
            serializer.instance.nome if serializer.instance else None
        )
        categoria = serializer.validated_data.get("categoria") or (
            serializer.instance.categoria if serializer.instance else None
        )
        if nome is None or categoria is None:
            return

        irmaos = Salgado.objects.filter(categoria=categoria, nome=nome)
        if serializer.instance is not None:
            irmaos = irmaos.exclude(pk=serializer.instance.pk)
        if irmaos.exists():
            raise ValidationError(
                f'Ja existe "{nome}" em {categoria.nome}.'
            )

    def recusar_categoria_de_outra_conta(self, serializer):
        categoria = serializer.validated_data.get("categoria")
        conta = get_conta_do_usuario(self.request.user)
        if categoria and conta and categoria.conta_id != conta.id:
            raise ValidationError(
                {"categoria": "Essa categoria nao e da sua empresa."}
            )

    def perform_create(self, serializer):
        self.recusar_categoria_de_outra_conta(serializer)
        self.recusar_nome_repetido(serializer)
        serializer.save()

    def perform_update(self, serializer):
        self.recusar_categoria_de_outra_conta(serializer)
        self.recusar_nome_repetido(serializer)
        serializer.save()
