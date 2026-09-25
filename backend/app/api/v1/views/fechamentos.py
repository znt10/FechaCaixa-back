"""Fechamento de caixa: lancamento publico pela loja, leitura pela gerencia."""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from datetime import date

from django.utils import timezone

from app.feriados import eh_domingo, motivo_de_turno_unico

from app.models import (
    Encarregado,
    FechamentoCaixa,
    Loja,
    ResponsavelRetirada,
)
from app.permissions import (
    IsGerenteOrAdministrador,
    PodeConferirOCaixa,
    TemAcessoAoFormulario,
    get_conta_do_usuario,
    is_gerente_ou_admin,
)
from ..serializers import (
    EncarregadoSerializer,
    FechamentoCaixaCreateSerializer,
    FechamentoCaixaFormularioSerializer,
    FechamentoCaixaSerializer,
    FechamentoCaixaUpdateSerializer,
    LojaPublicaSerializer,
    ResponsavelRetiradaSerializer,
)


def escopo_de_lojas(request):
    """Lojas ativas que este request pode enxergar.

    Um lugar so para a regra, porque ela aparece em tres consultas diferentes
    (dropdown publico, pendentes, listagem) e divergir entre elas seria
    justamente o vazamento entre contas que a camada existe para impedir.
    """
    lojas = Loja.objects.filter(ativo=True)
    user = request.user

    if user.is_authenticated:
        if user.is_superuser:
            return lojas
        conta = get_conta_do_usuario(user)
        return lojas.filter(conta=conta) if conta else lojas.none()

    # Anonimo (funcionario no formulario): a conta vem do aparelho, nunca
    # do ?conta= da URL — ver TemAcessoAoFormulario.
    conta = getattr(request, "conta_do_formulario", None)
    if not conta:
        return lojas.none()
    return lojas.filter(conta=conta)


class ResponsavelRetiradaViewSet(viewsets.ModelViewSet):
    queryset = ResponsavelRetirada.objects.filter(ativo=True).order_by("nome")
    serializer_class = ResponsavelRetiradaSerializer
    lookup_field = 'public_id'
    # Sem paginacao: e cadastro de UMA conta, dezenas de linhas, e as duas
    # telas que leem mostram a lista inteira. Com o PAGE_SIZE de 50 do settings
    # a resposta vinha cortada no quinquagesimo nome — e a empresa com 61
    # pessoas perdia 11 delas, em ordem alfabetica. Na tela da empresa parecia
    # cosmetico; no formulario da loja essas 11 nao conseguiam fechar caixa nem
    # aparecer na lista de consumo.
    #
    # Subir o PAGE_SIZE so adiaria o mesmo bug para a proxima dezena.
    pagination_class = None

    def get_permissions(self):
        if self.action == 'list':
            # O painel logado continua listando normalmente (get_queryset ja
            # escopa pela conta do usuario). O formulario precisa da lista
            # pro seletor de retirada — mas so da conta do aparelho (ver
            # get_queryset), nunca de qualquer um.
            if self.request.user.is_authenticated:
                return [IsAuthenticated()]
            return [TemAcessoAoFormulario()]
        return [IsAuthenticated(), IsGerenteOrAdministrador()]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if user.is_authenticated:
            if user.is_superuser:
                return queryset
            conta = get_conta_do_usuario(user)
            return queryset.filter(conta=conta) if conta else queryset.none()

        # Anonimo (funcionario no formulario): a conta vem do aparelho.
        conta = getattr(self.request, "conta_do_formulario", None)
        if not conta:
            return queryset.none()
        return queryset.filter(conta=conta)

    def perform_create(self, serializer):
        conta = get_conta_do_usuario(self.request.user)
        if conta is None:
            raise PermissionDenied(
                "Este usuario nao esta vinculado a uma conta. "
                "Cadastre o responsavel pelo /admin/, escolhendo a conta."
            )
        serializer.save(conta=conta)



class EncarregadoViewSet(viewsets.ModelViewSet):
    """Quem trabalha na loja: registra consumo, e alguns lancam o caixa.

    Mesmo desenho do ResponsavelRetiradaViewSet logo acima, e pela mesma razao:
    a lista tem duas pontas. O painel logado administra o cadastro; o
    formulario, que nao tem login, precisa dela para o seletor de quem esta
    lancando — e so da conta do proprio aparelho.
    """

    queryset = Encarregado.objects.filter(ativo=True).order_by("nome")
    serializer_class = EncarregadoSerializer
    lookup_field = "public_id"

    # Ver a nota no ResponsavelRetiradaViewSet: esta e a lista que quebrou em
    # producao, e a que mais doi — ela alimenta o seletor de quem lanca o caixa.
    pagination_class = None

    def get_permissions(self):
        if self.action == "list":
            if self.request.user.is_authenticated:
                return [IsAuthenticated()]
            return [TemAcessoAoFormulario()]
        return [IsAuthenticated(), IsGerenteOrAdministrador()]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if user.is_authenticated:
            if user.is_superuser:
                return queryset
            conta = get_conta_do_usuario(user)
            return queryset.filter(conta=conta) if conta else queryset.none()

        conta = getattr(self.request, "conta_do_formulario", None)
        if not conta:
            return queryset.none()
        return queryset.filter(conta=conta)

    def perform_create(self, serializer):
        # A conta vem de quem esta logado, nunca do payload.
        serializer.save(conta=get_conta_do_usuario(self.request.user))


class FechamentoCaixaViewSet(viewsets.ModelViewSet):
    queryset = FechamentoCaixa.objects.select_related(
        "loja", "responsavel_retirada", "editado_por"
    # Consumos e despesas vao aninhados na resposta: sem o prefetch, a tela de
    # saidas de um mes faria uma consulta por turno. As despesas ainda entram na
    # conta de `total`, entao sem elas aqui cada linha do painel custaria uma
    # consulta so para fechar o proprio total.
    ).prefetch_related(
        "retiradas__responsavel",
        "consumos__encarregado",
        "despesas",
        "desperdicios__salgado__categoria",
    )
    lookup_field = 'public_id'
    # Sem DELETE: apagar escondia o lancamento errado sem deixar rastro, e foi
    # exatamente esse buraco que motivou a acao `cancelar` abaixo. Ela e o
    # unico jeito de desfazer um fechamento a partir de agora.
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options', 'trace']

    def get_serializer_class(self):
        if self.action == 'create':
            return FechamentoCaixaCreateSerializer
        if self.action in ('update', 'partial_update'):
            return FechamentoCaixaUpdateSerializer
        return FechamentoCaixaSerializer

    def get_permissions(self):
        if self.action == 'cancelar':
            # Endpoint de dois donos: a loja sem login (dentro da janela) e a
            # gerencia logada. Nenhuma das duas permissoes decide os dois
            # casos sozinha, entao a escolha aqui e so "por qual porta esta
            # chegando" — TemAcessoAoFormulario tambem registra o uso do
            # aparelho (registrar_uso), efeito que se perderia se este ponto
            # fosse AllowAny. Quem cancela de fato e o que a acao confere no
            # corpo (pela_loja / is_gerente_ou_admin).
            if self.request.user.is_authenticated:
                return [IsAuthenticated(), PodeConferirOCaixa()]
            return [TemAcessoAoFormulario()]
        if self.action in ('create', 'turnos'):
            # Outros endpoints de dois donos, pela mesma razao do cancelar. A
            # loja lanca o dia de hoje pelo aparelho, sem login; a gerencia
            # repoe pelo painel um dia que a loja esqueceu, escolhendo a data
            # — e para escolher a data ela precisa perguntar antes quais
            # turnos existem naquele dia.
            # Funcionario logado nao entra: ele confere o caixa, nao lanca.
            if self.request.user.is_authenticated:
                return [IsAuthenticated(), IsGerenteOrAdministrador()]
            return [TemAcessoAoFormulario()]
        if self.action == 'correcao':
            # Quem lanca na loja nao tem login de usuario, mas precisa do
            # aparelho ja ter entrado com o codigo da empresa.
            return [TemAcessoAoFormulario()]
        # Ler, conferir e corrigir o caixa lancado: gerencia e funcionario.
        # O funcionario para aqui — administrar a empresa (codigo, aparelhos,
        # quem retira dinheiro, quem tem login) segue exigindo gerencia, em
        # cada uma daquelas views.
        return [IsAuthenticated(), PodeConferirOCaixa()]

    def get_queryset(self):
        queryset = super().get_queryset()

        # O fechamento nao guarda conta: ela vem da loja. Isolar por
        # loja__conta evita um campo redundante que poderia divergir da loja.
        user = self.request.user
        if not user.is_superuser:
            conta = get_conta_do_usuario(user)
            queryset = queryset.filter(loja__conta=conta) if conta else queryset.none()

        params = self.request.query_params
        if params.get('loja'):
            queryset = queryset.filter(loja__public_id=params['loja'])
        if params.get('data'):
            queryset = queryset.filter(data=params['data'])
        if params.get('mes'):  # formato YYYY-MM
            ano, _, mes = params['mes'].partition('-')
            queryset = queryset.filter(data__year=ano, data__month=mes)
        # Intervalo livre: a tela de graficos precisa de "semana", que nao cabe
        # nem em ?data= nem em ?mes=. Com de/ate ela pede os tres periodos pelo
        # mesmo caminho.
        if params.get('de'):
            queryset = queryset.filter(data__gte=params['de'])
        if params.get('ate'):
            queryset = queryset.filter(data__lte=params['ate'])
        if params.get('periodo'):
            queryset = queryset.filter(periodo=params['periodo'])
        return queryset

    def perform_update(self, serializer):
        serializer.save(editado_por=self.request.user)

    @action(detail=True, methods=['get', 'patch'], url_path='correcao')
    def correcao(self, request, public_id=None):
        """Janela de 20 minutos em que a propria loja conserta o que mandou.

        Nao passa pelo get_queryset porque o funcionario nao tem login de
        usuario — para ele o queryset do painel e vazio de proposito. Quem
        faz as vezes de credencial e o public_id (uuid4, imprevisivel),
        devolvido so a quem acabou de enviar; e o mesmo grau de segredo que
        ja protege o link do formulario. Depois dos 20 minutos nem o id
        serve mais. O aparelho ainda precisa ter entrado com o codigo da
        empresa (TemAcessoAoFormulario), e o serializer confere que a loja
        do payload e da mesma conta do aparelho.

        A busca e dentro da conta do aparelho, e nao pelo public_id sozinho:
        o id nao diz de quem e o lancamento, e conferir so a loja do payload
        deixava passar o caminho inverso — mandar o id da empresa vizinha com
        uma loja propria no corpo movia o fechamento dela para ca.
        """
        fechamento = get_object_or_404(
            FechamentoCaixa,
            public_id=public_id,
            loja__conta=request.conta_do_formulario,
        )

        if not fechamento.pode_ser_corrigido_pela_loja:
            return Response(
                {
                    "detail": (
                        "O prazo para corrigir este lancamento terminou. "
                        "Peca para a gerencia ajustar."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if request.method == 'GET':
            return Response(FechamentoCaixaFormularioSerializer(fechamento).data)

        serializer = FechamentoCaixaCreateSerializer(
            fechamento, data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        # Sem carimbar editado_por: dentro da janela isto ainda e a loja
        # lancando, nao a gerencia corrigindo. O campo existe justamente para
        # marcar quando o numero deixou de ser o que a loja mandou.
        serializer.save()
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='cancelar')
    def cancelar(self, request, public_id=None):
        """Desfaz um lancamento sem apagar: some dos relatorios, o turno libera.

        Um endpoint para dois donos. A loja desfaz o que acabou de mandar,
        dentro da janela de 20 minutos e sem login — do mesmo jeito que ja
        corrige, com o public_id fazendo as vezes de senha. A gerencia desfaz
        a qualquer momento, com login, e fica registrada em cancelado_por.

        Funcionario com login NAO cancela: ele confere o caixa, nao desfaz.
        Ate agora o DELETE do ModelViewSet deixava — ver http_method_names
        acima, no topo da classe.

        Busca em `todos` e nao em `objects` porque o manager padrao esconde
        cancelado: sem isso, cancelar de novo daria 404 em vez de responder
        que ja esta cancelado.
        """
        conta_do_aparelho = getattr(request, "conta_do_formulario", None)
        pela_loja = not request.user.is_authenticated

        if pela_loja:
            if not conta_do_aparelho:
                raise PermissionDenied(
                    "Este aparelho nao esta conectado a nenhuma empresa."
                )
            fechamento = get_object_or_404(
                FechamentoCaixa.todos,
                public_id=public_id,
                loja__conta=conta_do_aparelho,
            )
            if not fechamento.cancelado and not fechamento.pode_ser_corrigido_pela_loja:
                return Response(
                    {
                        "detail": (
                            "O prazo para cancelar este lancamento terminou. "
                            "Peca para a gerencia."
                        )
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            if not is_gerente_ou_admin(request.user):
                raise PermissionDenied(
                    "So a gerencia pode cancelar um lancamento ja conferido."
                )
            conta = get_conta_do_usuario(request.user)
            fechamento = get_object_or_404(
                FechamentoCaixa.todos,
                public_id=public_id,
                **({} if request.user.is_superuser else {"loja__conta": conta}),
            )

        # Idempotente: o botao pode ser tocado duas vezes num celular ruim, e a
        # segunda nao pode reescrever quem cancelou nem quando.
        if not fechamento.cancelado:
            fechamento.cancelado_em = timezone.now()
            fechamento.cancelado_por = None if pela_loja else request.user
            fechamento.save(update_fields=["cancelado_em", "cancelado_por"])

        return Response({"cancelado_em": fechamento.cancelado_em})

    @action(detail=False, methods=['get'], url_path='turnos')
    def turnos(self, request):
        """Turnos que existem num dia — a regra de domingo/feriado mora no
        backend, e o formulario so pergunta.

        Duplicar a lista de feriados no JavaScript daria duas versoes da mesma
        regra, e uma delas ia ficar para tras.
        """
        bruto = request.query_params.get('data')
        try:
            dia = date.fromisoformat(bruto) if bruto else timezone.localdate()
        except ValueError:
            return Response({"detail": "Data invalida."}, status=400)

        # A conta vem do aparelho quem pergunta e a loja, e do login quando
        # quem pergunta e o painel. Nos dois casos e a conta inteira, e nao so
        # o id: o regime de fechamentos por dia esta nela, e resolve-lo so no
        # caminho do aparelho fazia o painel de uma empresa de um fechamento
        # por dia receber manha e tarde — dois turnos que o proprio serializer
        # recusaria no envio.
        conta_do_aparelho = getattr(request, "conta_do_formulario", None)
        primeira_loja = escopo_de_lojas(request).select_related('conta').first()
        conta = conta_do_aparelho or (primeira_loja.conta if primeira_loja else None)

        # Empresa que fecha o caixa uma vez por dia so tem um periodo, e a
        # regra de domingo/feriado nao se aplica a ela: essa regra fala de
        # manha e tarde, e aqui nao existe tarde para tirar.
        if conta and conta.fechamentos_por_dia == 1:
            return Response(
                {
                    "data": dia,
                    "periodos": [FechamentoCaixa.Periodo.DIA],
                    "motivo": None,
                }
            )

        motivo = motivo_de_turno_unico(dia, conta.id if conta else None)

        # Domingo e feriado nao viram "manha": a loja abre mais tarde e fecha
        # mais tarde, entao o unico lancamento do dia cobre o expediente todo.
        # Domingo tem turno proprio; feriado usa o do dia inteiro.
        if motivo:
            periodos = [
                FechamentoCaixa.Periodo.DOMINGO
                if eh_domingo(dia)
                else FechamentoCaixa.Periodo.DIA
            ]
        else:
            periodos = [
                FechamentoCaixa.Periodo.MANHA,
                FechamentoCaixa.Periodo.TARDE,
            ]
        return Response({"data": dia, "periodos": periodos, "motivo": motivo})

    @action(detail=False, methods=['get'], url_path='pendentes')
    def pendentes(self, request):
        """Lojas que ainda nao lancaram uma data/periodo — base do alerta do painel."""
        data = request.query_params.get('data')
        periodo = request.query_params.get('periodo')
        if not data:
            return Response({"detail": "Informe data."}, status=400)

        lojas = escopo_de_lojas(request)

        lancadas = FechamentoCaixa.objects.filter(data=data, loja__in=lojas)
        if periodo:
            lancadas = lancadas.filter(periodo=periodo)

        pendentes = lojas.exclude(
            pk__in=lancadas.values_list('loja_id', flat=True)
        ).order_by('nome_loja')
        return Response(LojaPublicaSerializer(pendentes, many=True).data)

    @action(detail=True, methods=['patch'], url_path='conferir')
    def conferir(self, request, public_id=None):
        """Marca o turno como conferido pelo gerente.

        Conferir e diferente de existir lancamento: o gerente vai virando
        manha, tarde e noite e marcando o que ja olhou. Fica separado do PATCH
        normal para que marcar como visto nao conte como correcao.
        """
        fechamento = self.get_object()
        fechamento.conferido = True
        fechamento.conferido_por = request.user
        fechamento.conferido_em = timezone.now()
        fechamento.save(
            update_fields=['conferido', 'conferido_por', 'conferido_em', 'updated_at']
        )
        return Response(self.get_serializer(fechamento).data)
