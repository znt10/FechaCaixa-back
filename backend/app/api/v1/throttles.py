"""Limites de taxa das rotas abertas (cadastro e pedido de link de senha)."""

from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle


class RegistroRateThrottle(AnonRateThrottle):
    """Limita o cadastro publico (anonimo) usando a taxa 'registro'.

    Herda de AnonRateThrottle: usuarios autenticados (ex: admin criando
    gerentes) nao sao limitados por esta regra.
    """

    scope = "registro"


class SenhaRateThrottle(SimpleRateThrottle):
    """Limita o pedido de link de senha pelo email ALVO, nao por quem pede.

    Herdar de AnonRateThrottle nao servia: o get_cache_key dele devolve None
    para requisicao autenticada, ou seja, qualquer conta logada podia inundar
    a caixa de qualquer loja com links de redefinicao. Chavear pelo alvo poe o
    teto onde o dano acontece e vale para anonimo e logado igualmente.
    """

    scope = "senha"

    def get_cache_key(self, request, view):
        email = request.data.get("email")
        if isinstance(email, str) and email.strip():
            ident = email.strip().lower()
        else:
            # Sem email nao ha o que enviar; limita pela origem so para a rota
            # nao ficar sem teto nenhum.
            ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class _TetoDoAcessoAoFormularioThrottle(SimpleRateThrottle):
    """Base dos throttles do acesso ao formulario: teto pela origem, nao pelo usuario.

    Nao herda de AnonRateThrottle: o get_cache_key dele devolve None para
    requisicao autenticada, ou seja, qualquer conta logada no mesmo
    navegador (loja, gerente, admin) — e CookieJWTAuthentication autentica
    qualquer requisicao com access_token valido, AllowAny na view ou nao —
    ficaria livre para brutar o codigo de qualquer outra empresa em
    velocidade total. Chavear por get_ident vale para anonimo e logado
    igualmente.
    """

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class AcessoFormularioMinutoThrottle(_TetoDoAcessoAoFormularioThrottle):
    """Teto por minuto do codigo da empresa.

    O codigo tem 4 digitos depois do prefixo. Sem teto, sao 6561 tentativas —
    minutos de script. Com 5/min, seculos.
    """

    scope = "acesso-formulario-min"


class AcessoFormularioHoraThrottle(_TetoDoAcessoAoFormularioThrottle):
    """Teto por hora: o de minuto sozinho ainda deixa 300 tentativas/hora."""

    scope = "acesso-formulario-hora"
