from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.db.models import Prefetch, Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from app.grupos import GRUPO_GERENTE
from app.models import Loja, PerfilUsuario
from app.notifications.tasks import (
    enviar_email_definir_senha,
    validar_token_confirmacao,
)
from app.notifications.tokens import validar_token_senha
from app.permissions import (
    get_conta_do_usuario,
    get_user_group_name,
    is_admin,
    is_gerente,
    is_gerente_ou_admin,
)
from ..serializers import UsuarioSerializer
from ..throttles import RegistroRateThrottle, SenhaRateThrottle


# 🔹 USUÁRIO
class UsuarioViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UsuarioSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Admin ve todos; qualquer outro ve so a si mesmo.

        O gerente tinha um meio-termo: enxergava tambem os logins das lojas que
        ele administrava. Esses logins nao existem mais — a loja entra pelo
        codigo no aparelho. Para ver os funcionarios dele, o caminho e
        /funcionarios/, que escopa por conta.
        """
        user = self.request.user

        if is_admin(user):
            return User.objects.all()

        return User.objects.filter(id=user.id)

    @action(detail=False, methods=['get'], url_path='me')
    def me(self, request):
        """Quem esta logado, para a interface saber o que mostrar.

        Devolvia tambem a loja da pessoa, quando a loja tinha login proprio.
        Nao tem mais: os tres cargos daqui (Admin, Gerente, Funcionario) veem
        as lojas da empresa inteira, nao uma so.

        `modulos` diz o que a empresa contratou, e nao o que o cargo pode: sao
        duas perguntas diferentes, e a interface precisa das duas para decidir
        se desenha a aba. Vem aninhado porque a resposta desta rota e o lugar
        natural de um segundo modulo quando ele existir.

        Aqui em vez de a tela perguntar a uma rota do proprio modulo e ler a
        recusa como "nao tem": adivinhar assim confunde erro de rede com
        modulo desligado, e obriga uma chamada extra em toda carga de tela.
        """
        # Sem empresa vinculada nao ha modulo contratado: a flag mora na Conta.
        #
        # O superuser e a excecao, e nao por simetria: ModuloDeNotasAtivo o
        # libera de proposito, entao ele abre as rotas do modulo com 200. Dizer
        # "false" aqui fazia a resposta contradizer a permissao na mesma
        # sessao, e sumia com a aba justamente para quem precisa conferir o
        # modulo de fora da empresa.
        conta = get_conta_do_usuario(request.user)
        tem_notas = request.user.is_superuser or bool(
            conta and conta.modulo_notas_ativo
        )

        return Response({
            "id": request.user.id,
            "first_name": request.user.first_name,
            "email": request.user.email,
            "group": get_user_group_name(request.user),
            "modulos": {
                "notas_fiscais": tem_notas,
            },
        })

    def create(self, request, *args, **kwargs):
        return Response(
            {"detail": "Use /users/registrar/ para criar usuários."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED
        )

    @action(
        detail=False,
        methods=['post'],
        permission_classes=[AllowAny],
        throttle_classes=[RegistroRateThrottle],
    )
    def registrar(self, request):
        """POST /api/v1/user/registrar/ — cria gerente. So ADMIN usa.

        Nao existe mais cadastro publico. Responsavel nao se cria a mao: cada
        loja ganha o proprio login ao ser cadastrada, a partir do email dela.
        O endpoint segue AllowAny para responder 403 com explicacao em vez do
        401 seco do IsAuthenticated.

        Gerente nao cria gerente: o que ele cria e funcionario, em
        /funcionarios/ — um login que confere o caixa e nao administra a
        empresa. Criar um par com os mesmos poderes que os seus e outra
        decisao, e ela e do Admin.
        """
        data = request.data
        tipo_usuario = data.get('tipo_usuario')

        requester_is_admin = bool(
            request.user
            and request.user.is_authenticated
            and is_admin(request.user)
        )

        if not requester_is_admin:
            return Response(
                {"error": "Apenas um admin autenticado pode cadastrar usuarios."},
                status=status.HTTP_403_FORBIDDEN
            )

        if tipo_usuario != 'gerente':
            return Response(
                {"error": (
                    "So e possivel cadastrar gerente aqui. Cada loja recebe o "
                    "proprio acesso quando e cadastrada, usando o e-mail dela."
                )},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = self.get_serializer(data=data)
        if serializer.is_valid():
            user = serializer.save()

            from django.contrib.auth.models import Group
            # get_or_create e nao get: a fixture cria o grupo no boot, mas depender
            # disso faria um banco sem fixture responder 500 em vez de
            # cadastrar.
            user.groups.add(Group.objects.get_or_create(name=GRUPO_GERENTE)[0])

            # A empresa vem de quem esta criando, nunca do corpo do request:
            # senao "criar meu colega" viraria "criar um login dentro da
            # empresa do vizinho". Superuser nao tem conta — o gerente que ele
            # cria fica sem vinculo ate alguem liga-lo a uma no /admin/.
            conta = get_conta_do_usuario(request.user)
            if conta is not None:
                PerfilUsuario.objects.create(user=user, conta=conta)

            corpo = dict(serializer.data)
            corpo['detail'] = 'Conta criada.'
            return Response(corpo, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(
        detail=False,
        methods=['get'],
        url_path=r'confirmar/(?P<token>[^/]+)',
        permission_classes=[AllowAny],
    )
    def confirmar(self, request, token=None):
        """GET /api/v1/user/confirmar/<token>/ — ativa a conta do email."""
        try:
            user_id = validar_token_confirmacao(token)
        except signing.SignatureExpired:
            return Response(
                {"error": "Link de confirmacao expirado. Cadastre-se novamente."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except signing.BadSignature:
            return Response(
                {"error": "Link de confirmacao invalido."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.filter(id=user_id).first()
        if not user:
            return Response(
                {"error": "Usuario nao encontrado."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not user.is_active:
            user.is_active = True
            user.save(update_fields=['is_active'])

        return Response({"detail": "Conta confirmada. Voce ja pode fazer login."})

    @action(
        detail=False,
        methods=['post'],
        url_path=r'definir-senha/(?P<token>[^/]+)',
        permission_classes=[AllowAny],
    )
    def definir_senha(self, request, token=None):
        """POST /api/v1/user/definir-senha/<token>/ — define a senha e ativa."""
        try:
            user_id = validar_token_senha(token)
        except signing.SignatureExpired:
            return Response(
                {"error": "Link expirado. Peca um novo em 'Esqueci a senha'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except signing.BadSignature:
            return Response(
                {"error": "Link invalido ou ja utilizado."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        senha = request.data.get('password')
        if not isinstance(senha, str):
            # JSON aceita numero/lista/objeto; os validators do Django chamam
            # .lower() e estouram AttributeError (500) num endpoint aberto.
            return Response(
                {"password": ["Informe a senha como texto."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.filter(id=user_id).first()
        if not user:
            return Response(
                {"error": "Link invalido."}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            validate_password(senha, user)
        except ValidationError as erro:
            return Response(
                {"password": list(erro.messages)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(senha)
        user.is_active = True
        user.save(update_fields=['password', 'is_active'])
        return Response({"detail": "Senha definida. Voce ja pode entrar."})

    @action(
        detail=False,
        methods=['post'],
        url_path='esqueci-senha',
        permission_classes=[AllowAny],
        throttle_classes=[SenhaRateThrottle],
    )
    def esqueci_senha(self, request):
        """POST /api/v1/user/esqueci-senha/ — manda o link de definir senha.

        Responde 200 exista ou nao o email: responder 404 revelaria quais
        emails estao cadastrados.
        """
        email = request.data.get('email')
        if email is not None and not isinstance(email, str):
            # JSON aceita numero/lista/objeto; .strip() estouraria 500 aqui.
            return Response(
                {"email": ["Informe o e-mail como texto."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if email:
            user = User.objects.filter(email__iexact=email.strip()).first()
            # Conta inativa que nunca definiu senha e uma loja recem-criada cujo
            # link expirou: sem isso ela ficaria travada, sem como pedir outro.
            # Ja uma conta desativada de proposito tem senha utilizavel, entao
            # continua bloqueada.
            if user and (user.is_active or not user.has_usable_password()):
                enviar_email_definir_senha.delay(user.id)

        return Response(
            {"detail": "Se este email estiver cadastrado, enviamos o link."}
        )
