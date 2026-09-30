"""Extrato bancario: contas, categorias, transacoes, importacao e resumo.

So a gerencia ve o banco, e so com o modulo ligado na empresa. O isolamento
por empresa mora no get_queryset de cada viewset, como no resto da API: a
permissao diz quem entra, o queryset diz o que a pessoa ve.
"""

import datetime
import uuid

from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from app.models import (
    CategoriaDeMovimento,
    ContaBancaria,
    TipoDeMovimento,
    TransacaoBancaria,
)
from app.permissions import (
    IsGerenteOrAdministrador,
    ModuloDeBancoAtivo,
    get_conta_do_usuario,
)
from app.services.extratos import (
    ClassificacaoInvalida,
    RecusaDeExtrato,
    classificar,
    garantir_categorias_de_movimento,
    importar_extrato,
    resumir,
)

from ..serializers import (
    CategoriaDeMovimentoSerializer,
    ClassificacaoDaTransacaoSerializer,
    ContaBancariaSerializer,
    TransacaoBancariaSerializer,
)

# Um OFX de um mes de uma loja movimentada tem centenas de KB. O teto barra o
# arquivo errado (um PDF, um ZIP) de virar TextField gigante no banco.
TAMANHO_MAXIMO_DO_EXTRATO = 5 * 1024 * 1024

# Quem sobe varios meses de uma vez sobe uma duzia de arquivos, nao centenas.
LIMITE_DE_ARQUIVOS_POR_LOTE = 24


def _uuid_do_parametro(params, nome):
    """O UUID do parametro, ou None. Formato invalido e 400, e nao 500."""
    valor = params.get(nome)
    if not valor:
        return None
    try:
        return uuid.UUID(valor)
    except ValueError:
        raise ValidationError(
            {"error": f"O parametro '{nome}' deve ser um UUID valido."}
        )


def _data_do_parametro(params, nome):
    valor = params.get(nome)
    if not valor:
        return None
    try:
        return datetime.date.fromisoformat(valor)
    except ValueError:
        raise ValidationError(
            {"error": f"O parametro '{nome}' deve ser uma data no formato AAAA-MM-DD."}
        )


class AcessoAoBancoMixin:
    permission_classes = [IsAuthenticated, IsGerenteOrAdministrador, ModuloDeBancoAtivo]
    lookup_field = "public_id"

    def conta_do_pedido(self):
        """A empresa do login, com as categorias semeadas. None para o
        superuser (ve tudo) e para quem nao tem empresa (nao ve nada) — por
        isso todo get_queryset olha is_superuser antes."""
        conta = get_conta_do_usuario(self.request.user)
        if conta is not None:
            garantir_categorias_de_movimento(conta)
        return conta

    def conta_para_criar(self):
        conta = get_conta_do_usuario(self.request.user)
        if conta is None:
            raise ValidationError(
                "Este login não está ligado a nenhuma empresa. Entre pela "
                "empresa para a qual o cadastro deve ser criado."
            )
        return conta


class ContaBancariaViewSet(
    AcessoAoBancoMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """As contas bancarias das lojas da empresa.

    Sem DELETE: o extrato importado pendura na conta, e extrato e
    contabilidade. Conta que fechou se desativa.
    """

    serializer_class = ContaBancariaSerializer
    # Uma empresa tem uma ou duas contas por loja; a tela mostra todas.
    pagination_class = None

    def get_queryset(self):
        base = ContaBancaria.objects.select_related("loja")
        if self.request.user.is_superuser:
            return base.all()
        conta = self.conta_do_pedido()
        if conta is None:
            return ContaBancaria.objects.none()
        return base.filter(loja__conta=conta)

    def _recusar_repetida(self, serializer, loja):
        dados = serializer.validated_data
        instancia = serializer.instance
        repetidas = ContaBancaria.objects.filter(
            loja=loja,
            banco=dados.get("banco", getattr(instancia, "banco", None)),
            agencia=dados.get("agencia", getattr(instancia, "agencia", "")),
            numero=dados.get("numero", getattr(instancia, "numero", None)),
        )
        if instancia is not None:
            repetidas = repetidas.exclude(pk=instancia.pk)
        if repetidas.exists():
            raise ValidationError("Esta conta já está cadastrada nesta loja.")

    def perform_create(self, serializer):
        conta = self.conta_para_criar()
        loja = serializer.validated_data["loja"]
        # A loja do corpo do pedido pode ser da empresa vizinha: o id entra no
        # JSON do mesmo jeito. A recusa vem antes do save.
        if loja.conta_id != conta.id:
            raise ValidationError("Esta loja é de outra empresa.")
        self._recusar_repetida(serializer, loja)
        serializer.save()

    def perform_update(self, serializer):
        loja = serializer.validated_data.get("loja", serializer.instance.loja)
        # A conta nao muda de loja: o extrato ja importado e do CNPJ antigo, e
        # mover a conta levaria esse dinheiro para a loja errada.
        if loja.pk != serializer.instance.loja_id:
            raise ValidationError(
                "A loja de uma conta bancária não muda. Desative esta conta e "
                "cadastre a nova na loja certa."
            )
        self._recusar_repetida(serializer, loja)
        serializer.save()


class CategoriaDeMovimentoViewSet(
    AcessoAoBancoMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """As categorias de transferencia e recebimento da empresa.

    Sem DELETE, como o plano de contas se desativa: categoria em uso apagada
    deixaria transacao classificada sem categoria.
    """

    serializer_class = CategoriaDeMovimentoSerializer
    pagination_class = None

    def get_queryset(self):
        if self.request.user.is_superuser:
            return CategoriaDeMovimento.objects.all()
        conta = self.conta_do_pedido()
        if conta is None:
            return CategoriaDeMovimento.objects.none()
        return CategoriaDeMovimento.objects.filter(conta=conta)

    def _recusar_nome_repetido(self, serializer, conta, tipo):
        nome = serializer.validated_data.get("nome")
        if nome is None:
            return
        irmas = CategoriaDeMovimento.objects.filter(conta=conta, tipo=tipo, nome=nome)
        if serializer.instance is not None:
            irmas = irmas.exclude(pk=serializer.instance.pk)
        if irmas.exists():
            raise ValidationError(f'Já existe uma categoria chamada "{nome}".')

    def perform_create(self, serializer):
        conta = self.conta_para_criar()
        tipo = serializer.validated_data.get("tipo")
        if tipo is None:
            raise ValidationError("Escolha se a categoria é de transferência ou de recebimento.")
        self._recusar_nome_repetido(serializer, conta, tipo)
        serializer.save(conta=conta)

    def perform_update(self, serializer):
        instancia = serializer.instance
        # O tipo nao muda: as transacoes ja classificadas nela ficariam com o
        # tipo de um lado e a categoria do outro.
        tipo = serializer.validated_data.get("tipo", instancia.tipo)
        if tipo != instancia.tipo:
            raise ValidationError(
                "Uma categoria não troca de tipo. Crie outra no tipo certo."
            )
        self._recusar_nome_repetido(serializer, instancia.conta, tipo)
        serializer.save()


class TransacaoBancariaViewSet(
    AcessoAoBancoMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """As transacoes do extrato: listar, classificar, importar e resumir.

    Transacao nao se digita nem se apaga: ela vem do arquivo, pela action
    `importar`. O PATCH so classifica — ver ClassificacaoDaTransacaoSerializer.
    """

    serializer_class = TransacaoBancariaSerializer

    def _transacoes_da_conta(self):
        base = TransacaoBancaria.objects.select_related(
            "conta_bancaria__loja",
            "elemento__grupo",
            "categoria",
            "par__conta_bancaria__loja",
        )
        if self.request.user.is_superuser:
            return base.all()
        conta = self.conta_do_pedido()
        if conta is None:
            return TransacaoBancaria.objects.none()
        return base.filter(conta=conta)

    def _filtrar_periodo_e_lugar(self, transacoes):
        params = self.request.query_params
        loja = _uuid_do_parametro(params, "loja")
        if loja:
            transacoes = transacoes.filter(conta_bancaria__loja__public_id=loja)
        conta_bancaria = _uuid_do_parametro(params, "conta_bancaria")
        if conta_bancaria:
            transacoes = transacoes.filter(conta_bancaria__public_id=conta_bancaria)
        de = _data_do_parametro(params, "de")
        if de:
            transacoes = transacoes.filter(data__gte=de)
        ate = _data_do_parametro(params, "ate")
        if ate:
            transacoes = transacoes.filter(data__lte=ate)
        return transacoes

    def get_queryset(self):
        transacoes = self._filtrar_periodo_e_lugar(self._transacoes_da_conta())
        params = self.request.query_params

        # A fila de trabalho da tela: o que ainda falta classificar.
        if params.get("pendente") == "true":
            transacoes = transacoes.filter(tipo__isnull=True)

        tipo = params.get("tipo")
        if tipo:
            if tipo not in TipoDeMovimento.values:
                raise ValidationError(
                    {"error": "O parametro 'tipo' deve ser PAGAMENTO, TRANSFERENCIA ou RECEBIMENTO."}
                )
            transacoes = transacoes.filter(tipo=tipo)

        return transacoes

    def partial_update(self, request, *args, **kwargs):
        """Classifica a transacao e, se pedido, as pendentes de mesma descricao."""
        transacao = self.get_object()
        entrada = ClassificacaoDaTransacaoSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data

        elemento = dados.get("elemento")
        categoria = dados.get("categoria")
        # Comparado com a empresa da TRANSACAO, e nao a do login: vale tambem
        # para o superuser, que alcanca todas as empresas e nao pode misturar
        # o plano de contas de uma no extrato da outra.
        if elemento is not None and elemento.grupo.conta_id != transacao.conta_id:
            raise ValidationError({"error": "Este elemento de despesa é de outra empresa."})
        if categoria is not None and categoria.conta_id != transacao.conta_id:
            raise ValidationError({"error": "Esta categoria é de outra empresa."})

        try:
            iguais = classificar(
                transacao,
                dados["tipo"],
                elemento=elemento,
                categoria=categoria,
                aplicar_as_iguais=dados["aplicar_as_iguais"],
            )
        except ClassificacaoInvalida as erro:
            raise ValidationError({"error": erro.mensagem})

        transacao = self._transacoes_da_conta().get(pk=transacao.pk)
        resposta = TransacaoBancariaSerializer(transacao).data
        resposta["iguais_classificadas"] = iguais
        return Response(resposta)

    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def importar(self, request):
        """Sobe um ou mais extratos de uma conta e diz o que cada um trouxe.

        Resposta 200 mesmo com recusa, como nas notas: recusa de arquivo nao e
        erro da requisicao, e o resultado misto e o que a tela mostra.
        """
        id_da_conta = _uuid_do_parametro(request.data, "conta_bancaria")
        if id_da_conta is None:
            return Response({"error": "Escolha a conta bancária do extrato."}, status=400)

        contas = ContaBancaria.objects.select_related("loja__conta")
        if not request.user.is_superuser:
            conta = get_conta_do_usuario(request.user)
            if conta is None:
                return Response(
                    {"error": "Este usuario nao esta vinculado a uma empresa."},
                    status=403,
                )
            contas = contas.filter(loja__conta=conta)
        conta_bancaria = contas.filter(public_id=id_da_conta).first()
        if conta_bancaria is None:
            return Response({"error": "Conta bancária não encontrada."}, status=404)

        arquivos = request.FILES.getlist("arquivos")
        if not arquivos:
            return Response({"error": "Nenhum arquivo foi enviado."}, status=400)
        if len(arquivos) > LIMITE_DE_ARQUIVOS_POR_LOTE:
            return Response(
                {
                    "error": (
                        f"Envie no máximo {LIMITE_DE_ARQUIVOS_POR_LOTE} arquivos "
                        f"por vez. Este lote tem {len(arquivos)}."
                    )
                },
                status=400,
            )

        importados = []
        recusados = []
        for arquivo in arquivos:
            if arquivo.size > TAMANHO_MAXIMO_DO_EXTRATO:
                recusados.append({
                    "arquivo": arquivo.name,
                    "motivo": "O arquivo é grande demais para ser um extrato.",
                })
                continue
            try:
                resultado = importar_extrato(
                    conta_bancaria, arquivo.name, arquivo.read(), request.user
                )
            except RecusaDeExtrato as recusa:
                recusados.append({"arquivo": arquivo.name, "motivo": recusa.mensagem})
                continue
            importacao = resultado.importacao
            importados.append({
                "arquivo": arquivo.name,
                "novas": resultado.novas,
                "repetidas": resultado.repetidas,
                "pareadas": resultado.pareadas,
                "classificadas": resultado.classificadas,
                "periodo_de": importacao.periodo_de,
                "periodo_ate": importacao.periodo_ate,
            })

        return Response({"importados": importados, "recusados": recusados})

    @action(detail=False, methods=["get"])
    def resumo(self, request):
        """Totais por tipo e categoria no periodo.

        Consolidado quando nao ha filtro de loja nem de conta: ai as
        transferencias entre lojas saem da conta (ver `resumir`).
        """
        params = request.query_params
        consolidado = not params.get("loja") and not params.get("conta_bancaria")
        transacoes = self._filtrar_periodo_e_lugar(self._transacoes_da_conta())
        return Response(resumir(transacoes, consolidado))
