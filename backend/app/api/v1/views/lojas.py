from django.db.models import ProtectedError
from rest_framework import status, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from app.models import Loja

from app.permissions import (
    IsGerenteOrAdministrador,
    PodeAdministrarALoja,
    TemAcessoAoFormulario,
    get_conta_do_usuario,
    is_admin,
    is_gerente,
)
from ..serializers import LojaPublicaSerializer, LojaSerializer


# 🔹 LOJA
class LojaViewSet(viewsets.ModelViewSet):
    queryset = Loja.objects.all().order_by('id')
    serializer_class = LojaSerializer
    lookup_field = 'public_id'

    def get_queryset(self):
        user = self.request.user

        if user.is_authenticated:
            if user.is_superuser:
                # Dono da plataforma: enxerga as lojas de todas as contas.
                return Loja.objects.all().order_by('id')

            conta = get_conta_do_usuario(user)
            if conta is None:
                # Autenticado sem conta vinculada nao ve loja nenhuma. Isso
                # inclui um "Admin" de grupo que ninguem ligou a uma conta —
                # so o superuser e dono da plataforma.
                return Loja.objects.none()
            return Loja.objects.filter(conta=conta).order_by('id')

        # Anonimo: e o funcionario abrindo o formulario, e a conta vem do
        # aparelho (ver TemAcessoAoFormulario) — nunca mais do ?conta= da
        # URL, que deixava ler as lojas de qualquer empresa.
        conta = getattr(self.request, "conta_do_formulario", None)
        if not conta:
            return Loja.objects.none()

        return Loja.objects.filter(conta=conta, ativo=True).order_by('id')

    def get_serializer_class(self):
        # Para o anonimo sai so id e nome: o link do formulario e publico, e
        # endereco/e-mail/responsavel da loja nao sao.
        if not self.request.user.is_authenticated:
            return LojaPublicaSerializer
        return LojaSerializer

    def get_permissions(self):
        if self.action == 'list':
            # O painel logado continua listando normalmente (get_queryset ja
            # escopa pela conta do usuario). So quem nao esta logado — o
            # funcionario no formulario — precisa do aparelho ja logado.
            if self.request.user.is_authenticated:
                return [IsAuthenticated()]
            return [TemAcessoAoFormulario()]
        if self.action == 'create':
            # Criar loja e coisa de gerente/admin.
            return [IsAuthenticated(), IsGerenteOrAdministrador()]
        # Editar/apagar: gerente/admin, ou o responsavel na propria loja.
        return [IsAuthenticated(), PodeAdministrarALoja()]

    def perform_destroy(self, instance):
        """Loja com caixa lancado nao se apaga.

        FechamentoCaixa.loja e CASCADE: apagar a loja apagaria o caixa dela
        junto, e este e o unico ponto do sistema onde uma acao de tela poderia
        destruir contabilidade. Desativada, a loja ja some do painel e do
        formulario — nao ha o que a exclusao resolva que a desativacao nao
        resolva.

        A ordem "desative antes de apagar" NAO e checada aqui de proposito: ela
        e a sequencia da tela da empresa (o botao Apagar so aparece depois de
        desativar), e nao uma regra do sistema. Impor no servidor quebraria o
        Admin, que apaga direto — comportamento antigo, com teste em
        test_lojas.LojaDeleteTests.
        """
        lancamentos = instance.fechamentos_caixa.count()
        if lancamentos:
            raise ValidationError(
                f"Esta loja tem {lancamentos} lancamento(s) de caixa, e apagar "
                f"levaria todos junto. Ela pode ficar desativada: assim some "
                f"do painel e do formulario, e o historico continua."
            )

        instance.delete()

    def perform_create(self, serializer):
        user = self.request.user

        conta = get_conta_do_usuario(user)
        if conta is None:
            # Superuser nao tem conta propria: ele cria loja pelo /admin/,
            # escolhendo a conta na mao. Sem isto o insert quebraria com um
            # 500 de NOT NULL em vez de dizer o motivo.
            raise PermissionDenied(
                "Este usuario nao esta vinculado a uma conta. "
                "Crie a loja pelo /admin/, escolhendo a conta."
            )

        # A conta e a unica coisa que a view acrescenta. Aqui tambem se
        # decidia qual gerente "dono" a loja teria: quem criasse virava dono, e
        # so o dono escrevia nela. Essa regra era do Unistock, onde varios
        # gerentes dividiam as lojas de uma rede — e era ela que trancava a
        # gerente para fora das lojas que existiam antes dela.
        serializer.save(conta=conta)

    def destroy(self, request, *args, **kwargs):
        # O ProtectedError era da MovimentacaoEstoque, que saiu com o Unistock.
        # Nenhuma FK protege a Loja hoje — quem a segura e o caixa lancado, e
        # essa recusa esta no perform_destroy, com mensagem propria. O except
        # fica como rede: se alguem criar uma FK PROTECT amanha, o erro sai
        # explicado em vez de 500.
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {
                    "error": (
                        "Esta loja tem historico de movimentacao de estoque e "
                        "nao pode ser excluida. Edite a loja e marque-a como "
                        "inativa em vez de exclui-la."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )
