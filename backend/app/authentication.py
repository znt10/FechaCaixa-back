from django.contrib.auth import get_user_model
from rest_framework.authentication import (
    BaseAuthentication,
    SessionAuthentication,
    get_authorization_header,
)
from rest_framework_simplejwt.tokens import AccessToken

from app.excecoes import EmpresaDesativada, MensalidadeBloqueada

# O que a empresa travada ainda alcanca: saber quem esta logado, ver a
# mensalidade (o Pix e o "Ja paguei") e sair.
ROTAS_LIVRES_NO_BLOQUEIO = (
    "/api/v1/user/me/",
    "/api/v1/minha-mensalidade/",
    "/logout/",
    "/token/refresh/",
)

# O formulario da loja se identifica. No tablet da loja o gerente pode estar
# logado tambem, e o cookie dele iria junto nos pedidos do formulario: sem
# este cabecalho, a empresa bloqueada perderia o caixa do dia, que a
# especificacao manda preservar. Com ele, o pedido segue anonimo e quem
# decide e o cookie do aparelho (TemAcessoAoFormulario). Nao abre furo: o
# anonimo nao alcanca nada do painel.
CABECALHO_DO_FORMULARIO = "X-Formulario"


class TravaDaMensalidade:
    """Barra o painel da empresa bloqueada por atraso ou desativada."""

    def aplicar_trava(self, request, resultado):
        if resultado is None:
            return None
        usuario = resultado[0]
        if usuario.is_superuser:
            return resultado

        # Aqui dentro: este modulo e carregado pelo DRF, e os modelos (via
        # permissions e services) nao podem vir no import dele.
        from app.permissions import get_conta_do_usuario
        from app.services.mensalidade import TRAVAM_O_PAINEL, Situacao, leitura_da_conta

        conta = get_conta_do_usuario(usuario)
        if conta is None:
            return resultado
        situacao = leitura_da_conta(conta).situacao
        if situacao not in TRAVAM_O_PAINEL:
            return resultado
        if request.headers.get(CABECALHO_DO_FORMULARIO):
            return None
        if request.path.startswith(ROTAS_LIVRES_NO_BLOQUEIO):
            return resultado
        if situacao == Situacao.DESATIVADA:
            raise EmpresaDesativada()
        raise MensalidadeBloqueada()


class CookieJWTAuthentication(TravaDaMensalidade, BaseAuthentication):
    def authenticate(self, request):
        token = request.COOKIES.get('access_token')

        if not token:
            auth = get_authorization_header(request).split()
            if auth and len(auth) == 2:
                token = auth[1].decode() if isinstance(auth[1], bytes) else auth[1]

        if not token:
            return None

        try:
            access_token = AccessToken(token)

            user_id = access_token.get('user_id')
            if not user_id:
                return None

            User = get_user_model()
            user = User.objects.get(id=user_id)
        except Exception:
            return None

        # Fora do try: as recusas da trava sao respostas, e nao "token
        # invalido" para engolir.
        return self.aplicar_trava(request, (user, None))

    def authenticate_header(self, request):
        # BaseAuthentication devolve None por padrao, e o DRF usa isso para
        # decidir o status de quem nao esta autenticado: sem header aqui, um
        # NotAuthenticated (401) legitimo vira 403 (ver APIView.handle_exception).
        # Como este e o primeiro autenticador da lista (DEFAULT_AUTHENTICATION_
        # CLASSES), toda rota sem token de aparelho ficava respondendo 403 em
        # vez de 401 — sem isto, TemAcessoAoFormulario nunca conseguiria dar
        # o 401 que o formulario fechado promete.
        return 'Bearer'


class SessaoComTravaDaMensalidade(TravaDaMensalidade, SessionAuthentication):
    """A sessao do Django na API, com a mesma trava do cookie JWT. O /admin
    usa a sessao dele e nao passa por aqui."""

    def authenticate(self, request):
        return self.aplicar_trava(request, super().authenticate(request))
