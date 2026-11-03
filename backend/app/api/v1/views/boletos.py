"""Os boletos a pagar: o envio da loja e o painel da gerencia."""

import datetime
import re
import uuid

from django.db.models import F
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from app.models import Boleto, Loja
from app.permissions import (
    IsGerenteOrAdministrador,
    ModuloDeBoletosAtivo,
    TemAcessoAoFormulario,
    get_conta_do_usuario,
)
from app.services.boletos.prazo import resumo_de_prazos
from app.services.boletos.registrar import BoletoRepetido, registrar_boleto
from app.services.comprovantes.arquivo import TAMANHO_MAXIMO, TIPOS_ACEITOS, ArquivoInvalido
from app.services.comprovantes.normalizar import valor_decimal

from ..serializers import BoletoSerializer


class Conflito(APIException):
    """409 no formato que a tela le ({"error": ...}), como o comprovante repetido."""

    status_code = 409

    def __init__(self):
        super().__init__({"error": "Este boleto já foi lançado."})


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
    texto = request.data.get("valor")
    if texto:
        valor = valor_decimal(texto)
        # Zero e "nao digitou": o campo de moeda apagado mostra 0,00, e
        # recusar aqui travaria a loja num campo opcional.
        if valor is None and re.sub(r"[^\d]", "", texto).strip("0"):
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
    except BoletoRepetido:
        raise Conflito()


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



class BoletoViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Os boletos da empresa, para a gerencia que paga.

    Nao se apagam pela API, se descartam: o descartado sai da lista, mas a foto
    continua guardada.
    """

    serializer_class = BoletoSerializer
    permission_classes = [IsAuthenticated, ModuloDeBoletosAtivo, IsGerenteOrAdministrador]
    # Sem paginacao: com o PAGE_SIZE de 50 do settings, o 51o boleto a pagar
    # sumiria da lista — e a lista existe para nenhum ficar esquecido.
    pagination_class = None
    lookup_field = "public_id"
    http_method_names = ["get", "patch", "post", "head", "options"]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def conta(self):
        return get_conta_do_usuario(self.request.user)

    def get_queryset(self):
        base = (
            Boleto.objects.select_related("loja", "pago_por")
            .defer("arquivo", "leitura_bruta")
            .exclude(situacao=Boleto.Situacao.DESCARTADO)
        )
        if self.request.user.is_superuser:
            return base
        conta = self.conta()
        return base.filter(conta=conta) if conta else Boleto.objects.none()

    def list(self, request, *args, **kwargs):
        if request.query_params.get("situacao") == Boleto.Situacao.PAGO:
            boletos = self.get_queryset().filter(situacao=Boleto.Situacao.PAGO).order_by("-pago_em")
        else:
            # Sem data primeiro: e o boleto que ninguem sabe quando vence, e
            # quem precisa abrir a foto para descobrir e a gerencia.
            boletos = self.get_queryset().filter(situacao=Boleto.Situacao.A_PAGAR).order_by(
                F("vencimento").asc(nulls_first=True), "created_at"
            )
        return Response(self.get_serializer(boletos, many=True).data)

    def create(self, request, *args, **kwargs):
        conta = self.conta()
        if conta is None:
            return Response({"error": "Este usuario nao esta vinculado a uma empresa."}, status=403)

        loja = None
        if request.data.get("loja"):
            loja = Loja.objects.filter(
                conta=conta, public_id=_uuid_ou_none(request.data["loja"])
            ).first()
            if loja is None:
                raise ValidationError({"error": "Esta loja nao existe nesta empresa."})

        boleto = _registrar(
            request, conta, loja, origem=Boleto.Origem.PAINEL, enviado_por=request.user
        )
        return Response(self.get_serializer(boleto).data, status=201)

    def perform_update(self, serializer):
        loja = serializer.validated_data.get("loja")
        if loja is not None and loja.conta_id != serializer.instance.conta_id:
            raise ValidationError({"error": "Esta loja nao existe nesta empresa."})

        boleto = serializer.instance
        editados = set(boleto.campos_editados) | set(serializer.validated_data)
        # Quem corrigiu olhou a foto: o "conferir" foi resolvido.
        serializer.save(campos_editados=sorted(editados), conferir=False)

    def _mudar(self, **campos):
        boleto = self.get_object()
        for campo, valor in campos.items():
            setattr(boleto, campo, valor)
        boleto.save(update_fields=[*campos, "updated_at"])
        return Response(self.get_serializer(boleto).data)

    @action(detail=True, methods=["post"])
    def pagar(self, request, public_id=None):
        return self._mudar(
            situacao=Boleto.Situacao.PAGO, pago_em=timezone.now(), pago_por=request.user
        )

    @action(detail=True, methods=["post"])
    def desfazer_pagamento(self, request, public_id=None):
        return self._mudar(situacao=Boleto.Situacao.A_PAGAR, pago_em=None, pago_por=None)

    @action(detail=True, methods=["post"])
    def descartar(self, request, public_id=None):
        return self._mudar(situacao=Boleto.Situacao.DESCARTADO)

    @action(detail=True, methods=["get"])
    def arquivo(self, request, public_id=None):
        # Busca de novo SEM o defer: a lista nao carrega os bytes.
        boleto = Boleto.objects.filter(pk=self.get_object().pk).only(
            "arquivo", "arquivo_tipo", "arquivo_nome"
        ).first()
        if boleto.arquivo is None:
            raise NotFound("Este boleto nao tem arquivo guardado.")
        resposta = HttpResponse(bytes(boleto.arquivo), content_type=boleto.arquivo_tipo)
        resposta["Content-Disposition"] = f'inline; filename="{boleto.arquivo_nome}"'
        resposta["X-Frame-Options"] = "SAMEORIGIN"
        return resposta

    @action(detail=False, methods=["get"])
    def resumo(self, request):
        return Response(resumo_de_prazos(self.get_queryset(), timezone.localdate()))
