from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework_simplejwt.tokens import AccessToken
from django.contrib.auth import get_user_model


class CookieJWTAuthentication(BaseAuthentication):
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
            return (user, None)
        except Exception:
            return None

    def authenticate_header(self, request):
        # BaseAuthentication devolve None por padrao, e o DRF usa isso para
        # decidir o status de quem nao esta autenticado: sem header aqui, um
        # NotAuthenticated (401) legitimo vira 403 (ver APIView.handle_exception).
        # Como este e o primeiro autenticador da lista (DEFAULT_AUTHENTICATION_
        # CLASSES), toda rota sem token de aparelho ficava respondendo 403 em
        # vez de 401 — sem isto, TemAcessoAoFormulario nunca conseguiria dar
        # o 401 que o formulario fechado promete.
        return 'Bearer'
