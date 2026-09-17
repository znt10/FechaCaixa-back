"""O unico endpoint publico do formulario: trocar o codigo por um aparelho."""

import hashlib
import secrets

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from app.models import Conta, DispositivoDoFormulario
from app.permissions import TemAcessoAoFormulario

from ..serializers import AcessoAoFormularioSerializer, EmpresaDoFormularioSerializer
from ..throttles import AcessoFormularioHoraThrottle, AcessoFormularioMinutoThrottle

COOKIE_DO_FORMULARIO = "formulario_token"
# Um numero so para os dois lados: o max_age que o navegador obedece e a
# validade que o servidor confere em ativo_por_token.
VALIDADE_DO_ACESSO = DispositivoDoFormulario.VALIDADE


def cookie_do_formulario(response, token):
    """Grava o token no aparelho com os mesmos parametros do login."""
    response.set_cookie(
        key=COOKIE_DO_FORMULARIO,
        value=token,
        max_age=int(VALIDADE_DO_ACESSO.total_seconds()),
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        path="/",
    )
    return response


def apelido_do_aparelho(request):
    """Um nome utilizavel para o aparelho, a partir do que o navegador conta.

    Serve a lista de "desconectar" do painel: o codigo e por empresa, e uma
    empresa com oito lojas junta oito aparelhos ali. Sem nome nenhum, escolher
    qual derrubar vira sorteio. Nao e identificacao — e etiqueta, e a gerencia
    renomeia no painel quando quiser.
    """
    agente = request.META.get("HTTP_USER_AGENT", "")
    for marca, nome in (
        ("iPhone", "iPhone"),
        ("iPad", "iPad"),
        ("Android", "Celular Android"),
        ("Windows", "Computador"),
        ("Macintosh", "Mac"),
        ("Linux", "Computador"),
    ):
        if marca in agente:
            return f"{nome} - {timezone.localdate().strftime('%d/%m/%Y')}"
    return f"Aparelho - {timezone.localdate().strftime('%d/%m/%Y')}"


class AcessoAoFormularioView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AcessoFormularioMinutoThrottle, AcessoFormularioHoraThrottle]

    def post(self, request):
        entrada = AcessoAoFormularioSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)

        conta = Conta.objects.filter(
            codigo_acesso=entrada.validated_data["codigo"], ativo=True
        ).first()
        if conta is None:
            # Mensagem unica para codigo errado e conta desativada: distinguir
            # os dois entrega a existencia da empresa a quem esta chutando.
            return Response(
                {"detail": "Codigo invalido."}, status=status.HTTP_401_UNAUTHORIZED
            )

        token = secrets.token_urlsafe(32)
        DispositivoDoFormulario.objects.create(
            conta=conta,
            apelido=(
                entrada.validated_data.get("apelido")
                or apelido_do_aparelho(request)
            ),
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )

        resposta = Response({"empresa": EmpresaDoFormularioSerializer(conta).data})
        return cookie_do_formulario(resposta, token)


class EmpresaDoFormularioView(APIView):
    """GET /api/v1/formulario/empresa/ — nome e config para o cabecalho."""

    permission_classes = [TemAcessoAoFormulario]

    def get(self, request):
        return Response(EmpresaDoFormularioSerializer(request.conta_do_formulario).data)


class SairDoFormularioView(APIView):
    """POST /api/v1/formulario/sair/ — desconecta so este aparelho."""

    permission_classes = [TemAcessoAoFormulario]

    def post(self, request):
        dispositivo = request.dispositivo_do_formulario
        dispositivo.revogado_em = timezone.now()
        dispositivo.save(update_fields=["revogado_em"])

        resposta = Response({"message": "Aparelho desconectado."})
        resposta.delete_cookie(COOKIE_DO_FORMULARIO, path="/", samesite="Lax")
        return resposta
