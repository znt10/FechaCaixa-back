import datetime
import uuid
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework import mixins, viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from app.models import NotaFiscal
from app.permissions import ModuloDeNotasAtivo, get_conta_do_usuario
from app.services.nfe import XmlNaoEhNFe
from app.services.notas import RecusaDeNota, importar_nota

from ..serializers import NotaFiscalSerializer

# Um XML de NFe tem dezenas de KB. Este teto para um arquivo grande mandado
# por engano (um ZIP renomeado, um PDF) de virar TextField gigante no banco.
# Nao e protecao contra bomba de entidades: um DOCTYPE malicioso cabe em
# poucas centenas de bytes e passa por este teto sem problema — quem barra
# isso e o guard de DOCTYPE dentro de app.services.nfe.ler_nfe.
TAMANHO_MAXIMO_DO_XML = 2 * 1024 * 1024

# Quem baixa a caixa de entrada do mes traz centenas de arquivos, nao
# milhares: um grupo de 8 lojas fica na casa das centenas por mes. O teto
# existe para que uma requisicao com 10 mil arquivos nao parseie e grave 10
# mil vezes numa chamada so.
LIMITE_DE_ARQUIVOS_POR_LOTE = 500


ZERO = Decimal("0.00")

# `max_digits` acima do campo (12) porque aqui o numero e uma SOMA: doze casas
# bastam para uma nota, nao para o mes inteiro de oito lojas somado.
DINHEIRO = DecimalField(max_digits=16, decimal_places=2)


class PaginacaoDeNotasComTotais(PageNumberPagination):
    """A pagina de notas, mais quanto a empresa gastou no filtro inteiro.

    A listagem pagina em 50, entao a tela nao tem como somar o que recebeu: num
    mes de 300 notas ela mostraria um terco do gasto com cara de total, e e por
    esse numero que a gerencia fecha o mes. A soma sai daqui, sobre o queryset
    ja filtrado, e nao de um endpoint proprio: totais e lista precisam ser do
    mesmo filtro e chegar juntos, senao a faixa de cima discorda da tabela de
    baixo enquanto o segundo pedido nao responde.

    Custa uma query a mais por pagina — um `aggregate` sem join, contra um
    indice que a listagem ja usa.
    """

    def paginate_queryset(self, queryset, request, view=None):
        # Aqui, e nao em `get_paginated_response`, porque e este o unico ponto
        # em que o queryset filtrado passa pelas maos do paginador; depois so
        # existe a pagina ja serializada.
        self.totais = self._somar(queryset)
        return super().paginate_queryset(queryset, request, view)

    def _somar(self, queryset):
        """Soma tudo e, de novo, so o que falta classificar.

        `Coalesce` em volta de cada `Sum` porque soma de conjunto vazio e NULL,
        e a tela recebe dinheiro: sem ele a faixa escreveria "R$ NaN" no mes em
        que nao ha pendencia nenhuma — justamente o mes em que esta tudo certo.
        """
        soma = queryset.aggregate(
            valor=Coalesce(
                Sum("valor_total", output_field=DINHEIRO),
                Value(ZERO),
                output_field=DINHEIRO,
            ),
            pendentes_valor=Coalesce(
                Sum(
                    "valor_total",
                    filter=Q(elemento__isnull=True),
                    output_field=DINHEIRO,
                ),
                Value(ZERO),
                output_field=DINHEIRO,
            ),
            pendentes_quantidade=Count("id", filter=Q(elemento__isnull=True)),
        )

        return {
            # String e nao float: dinheiro em float chega ao front como
            # 84320.099999 e a faixa arredonda o gasto do mes. E o mesmo
            # formato que `valor_total` ja tem em cada nota da lista.
            "valor": str(soma["valor"].quantize(ZERO)),
            "pendentes": {
                "quantidade": soma["pendentes_quantidade"],
                "valor": str(soma["pendentes_valor"].quantize(ZERO)),
            },
        }

    def get_paginated_response(self, data):
        resposta = super().get_paginated_response(data)
        resposta.data["totais"] = self.totais
        return resposta

    def get_paginated_response_schema(self, schema_do_item):
        """O `totais` tambem no schema: o front le a doc gerada para saber o
        que a rota devolve, e um campo fora dela e um campo que ninguem usa."""
        esquema = super().get_paginated_response_schema(schema_do_item)
        esquema["properties"]["totais"] = {
            "type": "object",
            "properties": {
                "valor": {"type": "string", "example": "84320.10"},
                "pendentes": {
                    "type": "object",
                    "properties": {
                        "quantidade": {"type": "integer", "example": 7},
                        "valor": {"type": "string", "example": "3410.00"},
                    },
                },
            },
        }
        return esquema


class NotaFiscalViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """As notas de entrada da conta: so leitura, mais a action `importar`.

    Monta-se por mixins e nao como ModelViewSet porque nota nao se digita nem
    se apaga: ela se importa do XML, e a unica criacao mora na action
    `importar`, que recebe arquivo. ModelViewSet abriria um POST e um DELETE
    que nenhuma tela usa e que ninguem lembraria de proteger.

    `UpdateModelMixin` voltou, mas so porque agora ha o que gravar: o
    serializer expoe um unico campo gravavel, `elemento_id`. Ele ja tinha
    saido uma vez justamente porque o PATCH aceitava qualquer corpo, nao
    gravava nada e respondia 200 — o pior modo de falha possivel numa tela,
    porque o front comemora e a nota continua como estava. Se um dia sobrar
    um serializer sem campo gravavel aqui, o mixin tem que sair de novo.
    """

    serializer_class = NotaFiscalSerializer
    permission_classes = [IsAuthenticated, ModuloDeNotasAtivo]
    pagination_class = PaginacaoDeNotasComTotais
    lookup_field = "public_id"

    def get_queryset(self):
        user = self.request.user
        base = NotaFiscal.objects.select_related(
            "loja", "fornecedor", "elemento", "elemento__grupo"
        )

        if user.is_superuser:
            notas = base.all()
        else:
            conta = get_conta_do_usuario(user)
            if conta is None:
                return NotaFiscal.objects.none()
            notas = base.filter(conta=conta)

        params = self.request.query_params

        # A fila de trabalho da tela: o que ainda falta classificar.
        classificada = params.get("classificada")
        if classificada == "false":
            notas = notas.filter(elemento__isnull=True)
        elif classificada == "true":
            notas = notas.filter(elemento__isnull=False)

        loja = params.get("loja")
        if loja:
            # UUID invalido (ex. "abc") faz `filter(loja__public_id=...)`
            # levantar django.core.exceptions.ValidationError, que vira 500.
            # Validar o formato aqui primeiro devolve 400 com a mensagem
            # certa, sem chegar a tocar o banco.
            try:
                uuid.UUID(loja)
            except ValueError:
                raise ValidationError(
                    {"error": "O parametro 'loja' deve ser um UUID valido."}
                )
            notas = notas.filter(loja__public_id=loja)

        de = params.get("de")
        if de:
            # Mesmo problema que `loja`: uma string que nao e data (ex.
            # "ontem") faz `filter(data_emissao__gte=...)` levantar
            # ValidationError do Django, que sem tratamento vira 500.
            try:
                datetime.date.fromisoformat(de)
            except ValueError:
                raise ValidationError(
                    {
                        "error": (
                            "O parametro 'de' deve ser uma data no formato "
                            "AAAA-MM-DD."
                        )
                    }
                )
            notas = notas.filter(data_emissao__gte=de)

        ate = params.get("ate")
        if ate:
            try:
                datetime.date.fromisoformat(ate)
            except ValueError:
                raise ValidationError(
                    {
                        "error": (
                            "O parametro 'ate' deve ser uma data no formato "
                            "AAAA-MM-DD."
                        )
                    }
                )
            notas = notas.filter(data_emissao__lte=ate)

        return notas

    def get_serializer(self, *args, **kwargs):
        """O front manda `elemento`; o serializer escreve por `elemento_id`.

        A traducao acontece aqui e nao no front porque a API publica deve ler e
        escrever o mesmo nome de campo: `elemento` na resposta e `elemento_id`
        no PATCH seria uma pegadinha para quem consome.
        """
        dados = kwargs.get("data")
        if dados is not None and "elemento" in dados:
            # `dict()` e nao `dados.copy()`: num corpo form-encoded o
            # request.data e um QueryDict, cuja copia continua QueryDict — e
            # `pop` num QueryDict devolve a LISTA de valores, nao a string.
            # O PATCH form-encoded morria em 400 dizendo que "['<uuid>']" nao
            # e um UUID, enquanto o mesmo pedido em JSON gravava.
            dados = dados.dict() if hasattr(dados, "dict") else dict(dados)
            dados["elemento_id"] = dados.pop("elemento")
            kwargs["data"] = dados
        return super().get_serializer(*args, **kwargs)

    def perform_update(self, serializer):
        """Classifica a nota, recusando elemento de outra empresa, e ensina o
        fornecedor.

        A checagem de conta mora aqui e nao no queryset do serializer porque
        estar logado limita quais NOTAS a pessoa alcanca, e nao o que cabe no
        corpo do pedido: o id de um elemento da empresa vizinha entra no JSON
        do mesmo jeito, e sem esta guarda seria aceito. A recusa vem ANTES do
        save, para que o pedido negado nao deixe rastro no banco.

        Ensinar o fornecedor e a regra de nao digitar duas vezes: da proxima
        nota desse fornecedor, o elemento ja vem preenchido. So aprende quando
        um elemento foi escolhido — limpar a classificacao nao apaga o que ja
        se sabia.
        """
        elemento = serializer.validated_data.get("elemento")
        conta = get_conta_do_usuario(self.request.user)

        if elemento is not None and not self.request.user.is_superuser:
            # `getattr(conta, "id", None)` cobre o usuario sem empresa
            # vinculada: para ele nenhum elemento e proprio, e a comparacao
            # com None recusa todos.
            if elemento.grupo.conta_id != getattr(conta, "id", None):
                raise ValidationError(
                    "Este elemento de despesa e de outra empresa."
                )

        nota = serializer.save()

        if elemento is not None:
            nota.fornecedor.elemento_sugerido = elemento
            nota.fornecedor.save(update_fields=["elemento_sugerido"])

    @action(
        detail=False,
        methods=["post"],
        parser_classes=[MultiPartParser, FormParser],
    )
    def importar(self, request):
        """Sobe um lote de XMLs e diz, arquivo por arquivo, o que aconteceu.

        Um lote e nao um arquivo por chamada porque quem baixa do e-mail baixa o
        mes inteiro. Cada arquivo e processado isolado, e a resposta sai 200
        mesmo com recusas: recusa nao e erro de requisicao, e o resultado
        misto e justamente o que a tela precisa mostrar.
        """
        conta = get_conta_do_usuario(request.user)
        if conta is None:
            return Response(
                {"error": "Este usuario nao esta vinculado a uma empresa."},
                status=403,
            )

        arquivos = request.FILES.getlist("arquivos")
        if not arquivos:
            return Response({"error": "Nenhum arquivo foi enviado."}, status=400)

        if len(arquivos) > LIMITE_DE_ARQUIVOS_POR_LOTE:
            return Response(
                {
                    "error": (
                        f"Envie no maximo {LIMITE_DE_ARQUIVOS_POR_LOTE} "
                        f"arquivos por vez. Este lote tem {len(arquivos)}."
                    )
                },
                status=400,
            )

        importadas = []
        recusadas = []

        for arquivo in arquivos:
            if arquivo.size > TAMANHO_MAXIMO_DO_XML:
                recusadas.append({
                    "arquivo": arquivo.name,
                    "motivo": "O arquivo e grande demais para ser um XML de nota.",
                })
                continue

            try:
                # Estrito, sem errors="replace": a NFe da SEFAZ e sempre
                # UTF-8, e um arquivo que nao decodifica assim vira recusa
                # daquele arquivo, nao erro da requisicao. "replace" fazia
                # dois estragos: corrompia em silencio um arquivo com byte
                # torto, e deixava passar disfarces — uma bomba de entidades
                # salva em UTF-16LE sobrevive a um decode com "replace" (e ao
                # decode estrito tambem, por sinal: todo byte de um UTF-16LE
                # so-ASCII e um codepoint UTF-8 valido sozinho) como uma
                # string ainda processavel. Quem barra essa disfarcada e o
                # guard dentro de app.services.nfe.ler_nfe, nao este decode.
                texto = arquivo.read().decode("utf-8")
            except UnicodeDecodeError:
                recusadas.append({
                    "arquivo": arquivo.name,
                    "motivo": (
                        "O arquivo nao esta em UTF-8, que e a codificacao da "
                        "nota fiscal eletronica."
                    ),
                })
                continue

            try:
                nota = importar_nota(texto, conta, request.user)
            except (RecusaDeNota, XmlNaoEhNFe) as recusa:
                recusadas.append({"arquivo": arquivo.name, "motivo": str(recusa)})
            else:
                importadas.append(nota)

        return Response({
            "importadas": NotaFiscalSerializer(importadas, many=True).data,
            "recusadas": recusadas,
        })
