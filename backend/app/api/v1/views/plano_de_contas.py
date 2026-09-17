"""O plano de contas da empresa: os grupos e os elementos de despesa.

E o que alimenta o seletor da tela de classificacao. Fica em modulo proprio,
e nao junto das notas, porque o plano existe independente de haver nota: a
empresa mexe nele antes de subir o primeiro XML.
"""

from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from app.models import ElementoDeDespesa, GrupoDeDespesa
from app.permissions import ModuloDeNotasAtivo, get_conta_do_usuario
from app.services.plano_de_contas import garantir_plano_de_contas

from ..serializers import ElementoDeDespesaSerializer, GrupoDeDespesaSerializer


class PlanoDeContasMixin:
    permission_classes = [IsAuthenticated, ModuloDeNotasAtivo]
    lookup_field = "public_id"
    # Sem paginacao: o plano de contas de uma empresa tem dezenas de linhas e a
    # tela mostra todas num seletor. Paginar aqui repetiria o bug que a lista de
    # encarregados ja teve, onde a pagina 2 simplesmente nao existia na tela.
    pagination_class = None

    def conta_do_pedido(self):
        conta = get_conta_do_usuario(self.request.user)
        if conta is not None:
            # Semeado ao listar tambem, e nao so no upload: quem abre a tela
            # antes da primeira nota veria um seletor vazio.
            garantir_plano_de_contas(conta)
        return conta


class GrupoDeDespesaViewSet(PlanoDeContasMixin, viewsets.ModelViewSet):
    serializer_class = GrupoDeDespesaSerializer

    def get_queryset(self):
        # is_superuser ANTES de get_conta_do_usuario: para o dono da
        # plataforma aquela funcao devolve None, que aqui significaria "nao ve
        # nada" em vez de "ve tudo".
        if self.request.user.is_superuser:
            return GrupoDeDespesa.objects.prefetch_related("elementos").all()

        conta = self.conta_do_pedido()
        if conta is None:
            return GrupoDeDespesa.objects.none()
        return GrupoDeDespesa.objects.filter(conta=conta).prefetch_related("elementos")

    def recusar_nome_repetido(self, serializer, conta):
        """Recusa o nome que ja existe nesta empresa, com 400 em vez de 500.

        O modelo tem a restricao `(conta, nome)` unica, mas o DRF nao monta a
        validacao dela sozinho: a `conta` entra pelo perform_create e nao pelo
        corpo do pedido, entao o validador nao tem os dois lados do par e a
        restricao do banco sobe crua. Criar "Insumos" duas vezes e o erro
        obvio de quem nao lembra se ja criou, e virava "erro inesperado" na
        tela.

        Unico DENTRO da empresa, e nao no sistema: "Insumos" e o nome obvio, e
        duas padarias diferentes vao usar o mesmo.
        """
        nome = serializer.validated_data.get("nome")
        if nome is None or conta is None:
            return

        irmaos = GrupoDeDespesa.objects.filter(conta=conta, nome=nome)
        # No PATCH, o proprio grupo nao conta como irmao: renomear "Insumos"
        # para "Insumos" nao e colisao.
        if serializer.instance is not None:
            irmaos = irmaos.exclude(pk=serializer.instance.pk)
        if irmaos.exists():
            raise ValidationError(
                f'Ja existe um grupo de despesa chamado "{nome}" nesta empresa.'
            )

    def perform_create(self, serializer):
        # A conta vem do usuario logado e nunca do corpo do pedido: quem
        # escolhe a empresa do grupo novo e o login, nao quem chama.
        conta = get_conta_do_usuario(self.request.user)
        if conta is None:
            # O superuser (e quem entrou sem empresa vinculada) cai aqui: para
            # ele aquela funcao devolve None de proposito, e gravar o grupo sem
            # empresa estourava no banco e virava 500. A API ainda nao tem por
            # onde escolher a empresa do grupo, entao a recusa explica o que
            # falta em vez de derrubar o pedido.
            raise ValidationError(
                "Este login nao esta ligado a nenhuma empresa. Entre pela "
                "empresa para a qual o grupo deve ser criado."
            )
        # Antes do save, para que o pedido negado nao deixe rastro no banco.
        self.recusar_nome_repetido(serializer, conta)
        serializer.save(conta=conta)

    def perform_update(self, serializer):
        # Renomear para um nome que ja existe e a mesma colisao pela outra
        # porta. O superuser alcanca grupos de qualquer empresa, entao a conta
        # aqui e a do grupo que ele esta editando, e nao a do login dele.
        self.recusar_nome_repetido(serializer, serializer.instance.conta)
        serializer.save()


class ElementoDeDespesaViewSet(PlanoDeContasMixin, viewsets.ModelViewSet):
    serializer_class = ElementoDeDespesaSerializer

    def get_queryset(self):
        if self.request.user.is_superuser:
            return ElementoDeDespesa.objects.select_related("grupo").all()

        conta = self.conta_do_pedido()
        if conta is None:
            return ElementoDeDespesa.objects.none()
        return ElementoDeDespesa.objects.select_related("grupo").filter(
            grupo__conta=conta
        )

    def recusar_grupo_de_outra_empresa(self, serializer):
        """Recusa o `grupo_id` que aponta para o plano de contas da vizinha.

        A checagem mora aqui e nao no queryset do `grupo_id` porque estar
        logado limita quais GRUPOS a pessoa alcanca pela listagem, e nao o que
        cabe no corpo do pedido: o id do grupo da empresa vizinha entra no JSON
        do mesmo jeito. Esse furo ja apareceu duas vezes neste projeto.

        Vale na criacao E na edicao: no PATCH o get_queryset impede alcancar o
        elemento alheio, mas nao impediria empurrar um elemento proprio para o
        grupo da vizinha — e ele sumiria do seletor de quem o criou.
        """
        grupo = serializer.validated_data.get("grupo")
        if grupo is None or self.request.user.is_superuser:
            return

        conta = get_conta_do_usuario(self.request.user)
        # `getattr(conta, "id", None)` cobre o usuario sem empresa vinculada:
        # para ele nenhum grupo e proprio, e a comparacao com None recusa
        # todos.
        if grupo.conta_id != getattr(conta, "id", None):
            raise ValidationError("Este grupo de despesa e de outra empresa.")

    def perform_create(self, serializer):
        # A recusa vem ANTES do save, para que o pedido negado nao deixe
        # rastro no banco.
        self.recusar_grupo_de_outra_empresa(serializer)
        serializer.save()

    def perform_update(self, serializer):
        self.recusar_grupo_de_outra_empresa(serializer)
        serializer.save()
