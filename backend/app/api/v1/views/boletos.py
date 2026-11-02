"""Os boletos a pagar: o envio da loja e o painel da gerencia."""

import datetime
import uuid

from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from app.models import Boleto, Loja
from app.permissions import TemAcessoAoFormulario
from app.services.boletos.registrar import registrar_boleto
from app.services.comprovantes.arquivo import TAMANHO_MAXIMO, TIPOS_ACEITOS, ArquivoInvalido
from app.services.comprovantes.normalizar import valor_decimal

from ..serializers import BoletoSerializer


def _uuid_ou_none(texto):
    try:
        return uuid.UUID(str(texto))
    except ValueError:
        return None


def _arquivo_ou_400(request):
    arquivo = request.FILES.get("arquivo")
    if arquivo is None:
        raise ValidationError({"error": "Tire a foto ou anexe o PDF do boleto."})
    if arquivo.content_type not in TIPOS_ACEITOS:
        # O HEIC da galeria do iPhone cai aqui: a camera pelo navegador ja
        # manda JPEG, e a frase diz o que fazer em vez de so recusar.
        raise ValidationError({"error": "Envie a foto do boleto em PDF, PNG, JPG ou WEBP."})
    if arquivo.size > TAMANHO_MAXIMO:
        raise ValidationError({"error": "O arquivo passa de 10 MB."})
    return arquivo


def _digitados(request):
    """Vencimento e valor como a pessoa mandou. Vazio e "nao sei"."""
    vencimento = None
    if request.data.get("vencimento"):
        try:
            vencimento = datetime.date.fromisoformat(request.data["vencimento"])
        except ValueError:
            raise ValidationError({"error": "O vencimento precisa ser uma data."})

    valor = None
    if request.data.get("valor"):
        valor = valor_decimal(request.data["valor"])
        if valor is None:
            raise ValidationError({"error": "O valor não parece dinheiro."})
    return vencimento, valor


def _registrar(request, conta, loja, **quem):
    arquivo = _arquivo_ou_400(request)
    vencimento, valor = _digitados(request)
    try:
        return registrar_boleto(
            conta,
            arquivo.read(),
            arquivo.content_type,
            arquivo.name,
            loja=loja,
            vencimento=vencimento,
            valor=valor,
            **quem,
        )
    except ArquivoInvalido:
        raise ValidationError({"error": "A foto não abriu. Tire outra."})


class BoletoDoFormularioView(APIView):
    """POST /api/v1/formulario/boletos/ — o boleto que chegou na loja.

    Envio proprio, fora do envio do turno: a foto fica guardada mesmo que o
    turno nao seja enviado, e uma leitura que falhe nao trava o fechamento.
    """

    permission_classes = [TemAcessoAoFormulario]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        conta = request.conta_do_formulario
        if not conta.boletos_ativos:
            return Response({"error": "Esta empresa não usa boletos a pagar."}, status=403)

        loja = Loja.objects.filter(
            conta=conta, ativo=True, public_id=_uuid_ou_none(request.data.get("loja"))
        ).first()
        if loja is None:
            raise ValidationError({"error": "Escolha a loja do boleto."})

        boleto = _registrar(
            request,
            conta,
            loja,
            origem=Boleto.Origem.FORMULARIO,
            dispositivo=request.dispositivo_do_formulario,
        )
        return Response(BoletoSerializer(boleto).data, status=201)

