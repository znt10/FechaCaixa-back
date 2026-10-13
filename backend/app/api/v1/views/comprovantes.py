import datetime
import os
import uuid
from decimal import Decimal

from django.conf import settings
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from app.models import Comprovante, Loja, RemetenteDeComprovante
from app.permissions import (
    IsGerenteOrAdministrador,
    ModuloDeComprovantesAtivo,
    get_conta_do_usuario,
)
from app.services.comprovantes.arquivo import TIPOS_ACEITOS
from app.services.comprovantes.categoria import aprender_categoria
from app.services.comprovantes.leitura import ler_arquivo
from app.services.comprovantes.normalizar import normalizar_nome
from app.services.comprovantes.registrar import registrar_comprovante

from ..serializers import ComprovanteSerializer, RemetenteDeComprovanteSerializer

ZERO = Decimal("0.00")
DINHEIRO = DecimalField(max_digits=16, decimal_places=2)
PERMISSOES = [IsAuthenticated, ModuloDeComprovantesAtivo, IsGerenteOrAdministrador]


def _data(params, nome):
    texto = params.get(nome)
    if not texto:
        return None
    try:
        return datetime.date.fromisoformat(texto)
    except ValueError:
        raise ValidationError(
            {"error": f"O parametro '{nome}' deve ser uma data no formato AAAA-MM-DD."}
        )


def _somar(queryset):
    """Totais do recorte (periodo + loja). So OK soma; conferir so conta."""
    ok = queryset.filter(situacao=Comprovante.Situacao.OK)
    total = ok.aggregate(
        valor=Coalesce(Sum("valor", output_field=DINHEIRO), Value(ZERO), output_field=DINHEIRO)
    )["valor"]
    por_loja = (
        ok.values("loja__public_id", "loja__nome_loja")
        .annotate(valor=Sum("valor", output_field=DINHEIRO))
        .order_by("loja__nome_loja")
    )
    return {
        "valor": str(total.quantize(ZERO)),
        "conferir": queryset.filter(situacao=Comprovante.Situacao.CONFERIR).count(),
        "por_loja": [
            {
                "loja": str(item["loja__public_id"]) if item["loja__public_id"] else None,
                "nome": item["loja__nome_loja"] or "Sem loja",
                "valor": str((item["valor"] or ZERO).quantize(ZERO)),
            }
            for item in por_loja
        ],
    }


def _uuid_ou_400(texto):
    try:
        return uuid.UUID(str(texto))
    except ValueError:
        raise ValidationError({"error": "O parametro 'loja' deve ser um UUID valido."})


class PaginacaoDeComprovantes(PageNumberPagination):
    def get_paginated_response(self, data):
        resposta = super().get_paginated_response(data)
        resposta.data["totais"] = self.totais
        return resposta


class ComprovanteViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Os comprovantes da empresa. Nascem do e-mail ou da action `enviar`;
    nao se apagam pela API, se descartam (o ID continua barrando o reenvio)."""

    serializer_class = ComprovanteSerializer
    permission_classes = PERMISSOES
    pagination_class = PaginacaoDeComprovantes
    lookup_field = "public_id"
    http_method_names = ["get", "patch", "post", "head", "options"]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        base = Comprovante.objects.select_related("loja", "elemento").defer("arquivo")
        if self.request.user.is_superuser:
            return base.all()
        conta = get_conta_do_usuario(self.request.user)
        if conta is None:
            return Comprovante.objects.none()
        return base.filter(conta=conta)

    def recorte(self, queryset):
        """Periodo e loja: o que vale para a lista E para os totais."""
        params = self.request.query_params
        de, ate = _data(params, "de"), _data(params, "ate")
        # Sem data lida (para conferir) entra em qualquer periodo: sumir do
        # filtro seria perder o comprovante de vista.
        if de:
            queryset = queryset.filter(Q(data_hora__date__gte=de) | Q(data_hora__isnull=True))
        if ate:
            queryset = queryset.filter(Q(data_hora__date__lte=ate) | Q(data_hora__isnull=True))

        loja = params.get("loja")
        if loja == "sem":
            queryset = queryset.filter(loja__isnull=True)
        elif loja:
            try:
                uuid.UUID(loja)
            except ValueError:
                raise ValidationError({"error": "O parametro 'loja' deve ser um UUID valido."})
            queryset = queryset.filter(loja__public_id=loja)
        return queryset

    def list(self, request, *args, **kwargs):
        recorte = self.recorte(self.get_queryset())
        lista = recorte
        situacao = request.query_params.get("situacao")
        if situacao:
            lista = lista.filter(situacao=situacao)

        self.paginator.totais = _somar(recorte)
        pagina = self.paginate_queryset(lista)
        return self.get_paginated_response(self.get_serializer(pagina, many=True).data)

    def perform_update(self, serializer):
        conta = serializer.instance.conta
        dados = serializer.validated_data
        loja = dados.get("loja")
        elemento = dados.get("elemento")
        if loja is not None and loja.conta_id != conta.id:
            raise ValidationError("Esta loja e de outra empresa.")
        if elemento is not None and elemento.grupo.conta_id != conta.id:
            raise ValidationError("Esta categoria e de outra empresa.")

        editados = set(serializer.instance.campos_editados) | (
            set(dados) - {"elemento"}  # categoria nao e "correcao da leitura"
        )
        extra = {"campos_editados": sorted(editados)}
        if "recebedor_nome" in dados:
            extra["recebedor_normalizado"] = normalizar_nome(dados["recebedor_nome"])[:200]
        comprovante = serializer.save(**extra)

        if "elemento" in dados:
            aprender_categoria(comprovante)

    @action(detail=True, methods=["post"])
    def confirmar(self, request, public_id=None):
        comprovante = self.get_object()
        if comprovante.valor is None or comprovante.data_hora is None:
            raise ValidationError("Preencha o valor e a data antes de confirmar.")
        comprovante.situacao = Comprovante.Situacao.OK
        comprovante.motivo = ""
        comprovante.save(update_fields=["situacao", "motivo", "updated_at"])
        return Response(self.get_serializer(comprovante).data)

    @action(detail=True, methods=["post"])
    def descartar(self, request, public_id=None):
        comprovante = self.get_object()
        comprovante.situacao = Comprovante.Situacao.DESCARTADO
        comprovante.save(update_fields=["situacao", "updated_at"])
        return Response(self.get_serializer(comprovante).data)

    @action(detail=True, methods=["post"], url_path="voltar-para-conferir")
    def voltar_para_conferir(self, request, public_id=None):
        """Desfaz um descarte: o comprovante volta para a fila e nao soma ate ser confirmado."""
        comprovante = self.get_object()
        if comprovante.situacao != Comprovante.Situacao.DESCARTADO:
            raise ValidationError("So um comprovante descartado volta para conferir.")
        comprovante.situacao = Comprovante.Situacao.CONFERIR
        comprovante.save(update_fields=["situacao", "updated_at"])
        return Response(self.get_serializer(comprovante).data)

    @action(detail=True, methods=["get"])
    def arquivo(self, request, public_id=None):
        # Busca de novo SEM o defer: a lista nao carrega os bytes.
        comprovante = Comprovante.objects.filter(
            pk=self.get_object().pk
        ).only("arquivo", "arquivo_tipo", "arquivo_nome").first()
        if comprovante.arquivo is None:
            raise NotFound("Este comprovante nao tem arquivo guardado.")
        resposta = HttpResponse(bytes(comprovante.arquivo), content_type=comprovante.arquivo_tipo)
        resposta["Content-Disposition"] = f'inline; filename="{comprovante.arquivo_nome}"'
        # O detalhe mostra o PDF num iframe da propria tela; o DENY padrao bloquearia.
        resposta["X-Frame-Options"] = "SAMEORIGIN"
        return resposta

    @action(detail=False, methods=["post"])
    def enviar(self, request):
        """Um arquivo pelo site: lido na hora e devolvido para a pessoa conferir."""
        conta = get_conta_do_usuario(request.user)
        if conta is None:
            return Response({"error": "Este usuario nao esta vinculado a uma empresa."}, status=403)

        arquivo = request.FILES.get("arquivo")
        if arquivo is None:
            raise ValidationError({"error": "Escolha o arquivo do comprovante."})
        if arquivo.content_type not in TIPOS_ACEITOS:
            raise ValidationError(
                {"error": "Envie o comprovante em PDF, PNG, JPG ou WEBP. "
                          "No iPhone, tire um print da foto e envie o print."}
            )

        loja = None
        if request.data.get("loja"):
            loja = Loja.objects.filter(
                conta=conta, public_id=_uuid_ou_400(request.data["loja"])
            ).first()
            if loja is None:
                raise ValidationError({"error": "Esta loja nao existe nesta empresa."})

        comprovante = registrar_comprovante(
            conta,
            arquivo.read(),
            arquivo.content_type,
            arquivo.name,
            origem=Comprovante.Origem.SITE,
            enviado_por=request.user,
            loja=loja,
            ler=ler_arquivo,
        )
        if comprovante is None:
            return Response({"error": "Este comprovante ja foi lancado."}, status=409)
        return Response(self.get_serializer(comprovante).data, status=201)

    @action(detail=False, methods=["get"])
    def configuracao(self, request):
        return Response({
            "endereco": settings.COMPROVANTES_EMAIL,
            "leitura_ligada": bool(os.getenv("ANTHROPIC_API_KEY")),
        })


class RemetenteDeComprovanteViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Os e-mails que podem mandar comprovante para a empresa."""

    serializer_class = RemetenteDeComprovanteSerializer
    permission_classes = PERMISSOES
    lookup_field = "public_id"
    pagination_class = None

    def get_queryset(self):
        conta = get_conta_do_usuario(self.request.user)
        if conta is None:
            return RemetenteDeComprovante.objects.none()
        return RemetenteDeComprovante.objects.filter(conta=conta)

    def perform_create(self, serializer):
        email = serializer.validated_data["email"].strip().lower()
        if RemetenteDeComprovante.objects.filter(email=email).exists():
            # Nao diz de qual empresa: seria vazar cliente para cliente.
            raise ValidationError({"email": "Este e-mail ja manda comprovante para outra empresa."})
        serializer.save(conta=get_conta_do_usuario(self.request.user), email=email)
