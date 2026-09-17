import hashlib
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

class BaseModel(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        abstract = True

    def soft_delete(self):
        self.is_deleted = True
        self.save()

class Conta(BaseModel):
    """Um negocio/cliente da plataforma. Ex: Marina, Primavera e Cia, Aurora Salgados.

    E o limite de visibilidade do sistema: loja, responsavel de retirada e
    fechamento pertencem a uma conta, e ninguem enxerga fora da propria.
    """

    nome = models.CharField(max_length=150)
    # Apelido que vai no link do formulario (/fechamento/marina). O public_id
    # tambem serve, mas ninguem cola um UUID num bilhete no balcao da loja.
    slug = models.SlugField(max_length=60, unique=True, null=True, blank=True)
    ativo = models.BooleanField(default=True)

    # O que a loja digita para abrir o formulario. Fica legivel no banco de
    # proposito: o dono precisa ler o codigo no painel para passar a loja.
    # A defesa e poder trocar (e derrubar os aparelhos), nao o sigilo do
    # armazenamento.
    codigo_acesso = models.CharField(max_length=16, unique=True, blank=True)

    # Existe cliente que fecha o caixa uma vez por dia, e nao de manha e de
    # tarde. Muda o formulario (some o seletor de turno) e o painel (a partir
    # de qual lancamento o dia vira "revisar").
    fechamentos_por_dia = models.PositiveSmallIntegerField(
        default=2,
        choices=[(1, "Um por dia"), (2, "Manha e tarde")],
    )

    # Liga o modulo de nota fiscal para esta conta. Default False: quem usa o
    # sistema hoje nao ve a tela nem alcanca a API, e o modulo vai sendo
    # ligado uma conta por vez, comecando pela que aceitou testar.
    modulo_notas_ativo = models.BooleanField(default=False)

    # Sem O/0 e I/1: o codigo e digitado a mao, no celular, as vezes por quem
    # esta lendo um bilhete escrito a caneta.
    LETRAS_DO_CODIGO = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    DIGITOS_DO_CODIGO = "23456789"

    def gerar_codigo_acesso(self):
        """Um codigo livre no formato LLLL-NNNN, derivado do nome da conta."""
        base = "".join(c for c in self.nome.upper() if c in self.LETRAS_DO_CODIGO)
        base = (base + "XXXX")[:4]
        for _ in range(50):
            numero = "".join(secrets.choice(self.DIGITOS_DO_CODIGO) for _ in range(4))
            codigo = f"{base}-{numero}"
            if not Conta.objects.filter(codigo_acesso=codigo).exclude(pk=self.pk).exists():
                return codigo
        # 50 colisoes seguidas com 8^4 combinacoes no mesmo prefixo nao
        # acontece por acaso. Se acontecer, troca o prefixo por letras
        # sorteadas (mesmo alfabeto sem O/I) e continua checando unicidade —
        # o fallback nao pode relaxar nenhuma das duas regras que o loop
        # existe para proteger.
        for _ in range(50):
            letras = "".join(secrets.choice(self.LETRAS_DO_CODIGO) for _ in range(4))
            numero = "".join(secrets.choice(self.DIGITOS_DO_CODIGO) for _ in range(4))
            codigo = f"{letras}-{numero}"
            if not Conta.objects.filter(codigo_acesso=codigo).exclude(pk=self.pk).exists():
                return codigo
        # 100 tentativas coincidindo (24**4 * 8**4 combinacoes possiveis) nao
        # e azar, e bug — gerar duplicata silenciosa seria pior que falhar.
        raise RuntimeError("Nao foi possivel gerar um codigo de acesso unico")

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._slug_livre(slugify(self.nome)[:55])
        if not self.codigo_acesso:
            self.codigo_acesso = self.gerar_codigo_acesso()
        return super().save(*args, **kwargs)

    def _slug_livre(self, base):
        base = base or "conta"
        slug, sufixo = base, 2
        while Conta.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base}-{sufixo}"
            sufixo += 1
        return slug

    def __str__(self):
        return self.nome


class Feriado(BaseModel):
    """Feriado local de uma conta — padroeira, aniversario da cidade, reforma.

    Os nacionais nao ficam aqui: sao calculados em app/feriados.py, iguais para
    todo mundo. Aqui entra so o que depende do municipio ou do negocio.
    """

    conta = models.ForeignKey(Conta, on_delete=models.CASCADE, related_name="feriados")
    data = models.DateField()
    descricao = models.CharField(max_length=120)

    class Meta:
        unique_together = ("conta", "data")
        ordering = ["data"]

    def __str__(self):
        return f"{self.data} - {self.descricao}"


class PerfilUsuario(BaseModel):
    """Liga um usuario (dono/gerente) a conta dele.

    Super admin (is_superuser) nao tem perfil de proposito: quem nao tem conta
    vinculada e ou dono da plataforma (ve tudo) ou nao ve nada. Nao existe
    meio-termo.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="perfil")
    conta = models.ForeignKey(Conta, on_delete=models.CASCADE, related_name="membros")

    def __str__(self):
        return f"{self.user.username} - {self.conta.nome}"


class Loja(BaseModel):
    # Obrigatorio: loja sem conta nao seria vista por ninguem. As lojas que
    # existiam antes da camada de conta foram adotadas na migracao 0026.
    conta = models.ForeignKey(Conta, on_delete=models.CASCADE, related_name="lojas")
    nome_loja = models.CharField(max_length=100)
    cidade = models.CharField(max_length=100)
    endereco = models.CharField(max_length=255)
    ativo = models.BooleanField(default=True)

    # O CNPJ e o que liga a nota fiscal a loja: o XML traz o CNPJ do
    # destinatario, e e por ele que a nota acha o lugar sozinha, sem ninguem
    # escolher numa lista e errar.
    #
    # NULL e nao "" de proposito: `unique` compara strings vazias entre si, e
    # a segunda loja sem CNPJ seria recusada. Toda loja que existe hoje esta
    # sem CNPJ e precisa continuar valida.
    #
    # max_length=18 aceita o CNPJ formatado que a gerente cola do contrato
    # (18 chars: "12.345.678/0001-99"), porque o validador do DRF e do admin
    # roda antes de save(). O que fica gravado sao so os 14 digitos, porque
    # save() normaliza.
    cnpj = models.CharField(max_length=18, null=True, blank=True, unique=True)

    @staticmethod
    def normalizar_cnpj(texto):
        """So os digitos, ou None. O contrato vem formatado, o XML vem cru.

        Levanta ValidationError se tem caracteres digitais demais ou nenhum
        digito (alem de vazio/None).
        """
        if not texto:
            return None
        digitos = "".join(c for c in str(texto) if c.isdigit())
        if digitos and len(digitos) != 14:
            raise ValidationError(
                f"CNPJ invalido: esperado 14 digitos, veio {len(digitos)}."
            )
        return digitos or None

    def save(self, *args, **kwargs):
        self.cnpj = self.normalizar_cnpj(self.cnpj)
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.nome_loja



class ResponsavelRetirada(BaseModel):
    """Quem pode retirar dinheiro do caixa — lista fechada, mantida pela gerencia.

    Existe como cadastro (e nao texto livre no fechamento) porque o nome de quem
    retirou e o que a gerencia cruza no fim do mes: texto livre viraria
    "Marina", "marina" e "Mari" para a mesma pessoa.
    """

    conta = models.ForeignKey(
        Conta, on_delete=models.CASCADE, related_name="responsaveis_retirada"
    )
    nome = models.CharField(max_length=100)
    ativo = models.BooleanField(default=True)

    class Meta:
        # A lista e lida por conta: no /admin do super admin ela vem misturada
        # de todas as empresas, e sem isto a ordem e a de insercao — as pessoas
        # de uma mesma conta ficam espalhadas pela pagina inteira. Agrupa por
        # conta e, dentro dela, por nome, que e como a gerente procura.
        ordering = ["conta__nome", "nome"]

    def __str__(self):
        return self.nome


class Encarregado(BaseModel):
    """Quem trabalha na loja: registra consumo, e alguns lancam o caixa.

    Nao e o "Funcionario" nem o "Gerente" do sistema — aqueles sao cargos de
    LOGIN, gente que entra no painel. Esta pessoa nao tem login: ela abre o
    formulario pelo codigo da empresa no aparelho da loja. Os tres nomes
    convivem na tela da empresa, e por isso este cadastro nao reusa nenhum dos
    dois.

    Existe pelo mesmo motivo do ResponsavelRetirada: quem lancava digitava o
    proprio nome a mao, e "Marina", "marina" e "Mari" eram a mesma pessoa para
    quem le e tres para quem soma. Somar o consumo do mes por pessoa nao fecha
    com texto livre.
    """

    conta = models.ForeignKey(
        Conta, on_delete=models.CASCADE, related_name="encarregados"
    )
    nome = models.CharField(max_length=100)

    # Tres perguntas diferentes, e nao uma. `ativo` e "ainda trabalha aqui";
    # este campo e "esta entre os que fecham o caixa" — o gerente, na tela da
    # empresa. A empresa tem 60 pessoas e 20 que lancam: juntar os dois campos
    # obrigaria a desativar 40 pessoas que estao trabalhando normalmente, e o
    # consumo delas sumiria junto.
    pode_lancar_caixa = models.BooleanField(default=True)

    # E "aparece na lista de consumo do formulario". Separado do de cima
    # porque gerente tambem come: os dois papeis se cruzam em vez de se
    # excluirem, e quem cadastra decide um de cada vez.
    pode_consumir = models.BooleanField(default=True)

    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["conta__nome", "nome"]

    def __str__(self):
        return self.nome


class SemCancelados(models.Manager):
    """O manager padrao do fechamento: cancelado nao existe.

    E acao a distancia, e isso e um custo real — quem le
    `FechamentoCaixa.objects.filter(...)` nao ve que algo esta sendo escondido.
    Aceito de proposito: a alternativa era filtrar em cada consulta, e a
    garantia passaria a depender de acertar seis lugares hoje e de quem
    escrever o setimo lembrar sozinho daqui a seis meses. O nome da classe
    aparece no modelo justamente para o leitor tropecar nela.
    """

    def get_queryset(self):
        return super().get_queryset().filter(cancelado_em__isnull=True)


class FechamentoCaixa(BaseModel):
    """O caixa de uma loja num dia/periodo, lancado pelo funcionario da loja.

    Lancar e publico (o funcionario nao tem login); corrigir e so de
    Admin/Gerente — dai o campo editado_por, que registra quem mexeu depois.
    """

    class Periodo(models.TextChoices):
        # A loja tem dois turnos. Nao existe turno da noite — existiu na
        # primeira versao do formulario e foi tirado (migracao 0032).
        MANHA = "MANHA", "Manha"
        TARDE = "TARDE", "Tarde"
        # Domingo e um turno proprio, e nao a manha esticada: a loja abre mais
        # tarde e fecha mais tarde, e o movimento e outro. Gravar como MANHA
        # somava esse caixa com as manhas de segunda a sabado, e a media por
        # manha saia errada — alem de esconder justamente o dia que a gerencia
        # quer olhar separado.
        DOMINGO = "DOMINGO", "Domingo"
        # Um lancamento que cobre o expediente inteiro. Vale para dois casos:
        # a empresa que fecha o caixa uma vez por dia, e o feriado de quem
        # fecha duas. Domingo tem valor proprio porque acontece toda semana e
        # a gerencia olha para ele sozinho; feriado cai aqui porque sao doze
        # datas no ano, cada uma com um nome diferente que nao cabe num enum.
        #
        # Gravar isso como MANHA faria o painel mentir: ele mostraria "Manha"
        # para um numero que e do dia inteiro, e cobraria uma tarde que nunca
        # vai chegar.
        DIA = "DIA", "Dia"

    loja = models.ForeignKey(
        Loja, on_delete=models.CASCADE, related_name="fechamentos_caixa"
    )
    # Quem lancou. O nome continua gravado ao lado, e nao e redundancia: os
    # lancamentos anteriores ao cadastro so tem o texto, e e ele que sete telas
    # ja leem. A FK e a verdade nova; o texto e a copia que mantem o historico
    # legivel.
    lancado_por = models.ForeignKey(
        Encarregado, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="fechamentos_lancados",
    )
    nome_funcionario = models.CharField(max_length=100)
    data = models.DateField()
    periodo = models.CharField(max_length=10, choices=Periodo.choices)

    # Zero e branco sao respostas validas (a loja pode nao ter vendido no PIX),
    # entao nenhuma forma de pagamento e obrigatoria.
    pix = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, default=0)
    # Credito e debito eram dois campos; viraram um so porque a loja fecha a
    # maquininha por um total, nao separado (migracao 0029 somou os antigos).
    cartao = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, default=0)
    dinheiro = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, default=0)
    link_pagamento = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, default=0)

    houve_retirada = models.BooleanField(default=False)
    responsavel_retirada = models.ForeignKey(
        ResponsavelRetirada, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="retiradas",
    )
    valor_retirado = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # A despesa nao mora mais aqui: virou linha propria (migracao 0044), pelo
    # mesmo motivo do consumo. Cabia uma so por turno, e o turno gasta com gas,
    # agua e remedio no mesmo expediente — o resto ia empilhado no campo de
    # texto, onde nao soma e nao se separa.

    # A unica pergunta cujo dinheiro saiu da gaveta E cancelou uma venda.
    houve_devolucao = models.BooleanField(default=False)
    devolucao_valor = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    houve_desperdicio = models.BooleanField(default=False)
    desperdicio_detalhes = models.TextField(null=True, blank=True)

    # "Ja olhei este turno" — diferente de existir lancamento. O gerente vai
    # virando turno por turno (manha, tarde, noite) e marcando o que conferiu.
    conferido = models.BooleanField(default=False)
    conferido_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="fechamentos_conferidos",
    )
    conferido_em = models.DateTimeField(null=True, blank=True)

    # Fica nulo enquanto ninguem corrigiu: presenca deste campo e a prova de
    # que o numero na tela nao e mais o que a loja lancou.
    editado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="fechamentos_editados",
    )

    # Cancelado nao e apagado: o lancamento fica, some dos relatorios, e o
    # turno volta a aceitar um envio novo. Quem cancelou e quando ficam
    # registrados porque desfazer numero de caixa e coisa que se audita.
    cancelado_em = models.DateTimeField(null=True, blank=True)
    # Nulo COM cancelado_em preenchido quer dizer que foi a propria loja, pelo
    # formulario — ela nao tem login de usuario, mesmo motivo de lancado_por
    # apontar para Encarregado e nao para User.
    cancelado_por = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fechamentos_cancelados",
    )

    # O painel, a planilha e os graficos chamam `.objects` explicitamente e
    # ganham o filtro de graca. Qual dos dois vira o manager "implicito"
    # (_default_manager/_base_manager, usados por delete em cascata e pelo
    # accessor reverso `loja.fechamentos_caixa`) e decidido a parte, no Meta.
    objects = SemCancelados()
    todos = models.Manager()

    @property
    def cancelado(self):
        return self.cancelado_em is not None

    class Meta:
        ordering = ["-data", "loja_id"]
        # base_manager_name="todos" e redundante por opcao: sem ele, o Django
        # ja cria sozinho um Manager simples e sem filtro para o
        # _base_manager (usado pelo Collector no DELETE em cascata) quando
        # nenhum manager herda de outro explicitamente — testado limpando
        # base_manager_name em memoria e inspecionando o manager resolvido,
        # a query vinha sem WHERE nenhum. A linha fica aqui para o leitor nao
        # precisar saber desse detalhe do Django, e para o dia em que os
        # managers deste modelo mudarem e essa garantia deixar de ser gratis.
        #
        # default_manager_name governa `loja.fechamentos_caixa` (o accessor
        # reverso usa o _default_manager, NAO o base_manager — testado: com
        # so base_manager_name="todos" o count() ainda vinha filtrado). E o
        # contador que impede apagar loja com historico (lojas.py) precisa
        # enxergar o cancelado, que sairia junto se a loja fosse apagada.
        base_manager_name = "todos"
        default_manager_name = "todos"

    FORMAS_DE_PAGAMENTO = ("pix", "cartao", "dinheiro", "link_pagamento")

    # Quanto tempo a loja tem para corrigir sozinha o que acabou de mandar.
    # Errar um digito e perceber acontece nos minutos seguintes, ainda com a
    # gaveta aberta; depois disso o numero ja entrou no relatorio da gerencia
    # e mexer nele deixa de ser correcao — vira mudanca sem rastro, e por isso
    # passa a ser so a gerencia (que fica registrada em editado_por).
    JANELA_DE_CORRECAO = timedelta(minutes=20)

    @property
    def segundos_para_corrigir(self):
        """Quanto sobra da janela de correcao da loja. Zero quando fechou.

        Conta do created_at, nao do updated_at: corrigir nao renova o prazo,
        senao bastaria salvar de novo a cada 19 minutos para editar sempre.
        """
        if self.conferido:
            # A gerencia ja olhou este turno: mudar por baixo desfaria a
            # conferencia sem ninguem ficar sabendo.
            return 0
        restante = (self.created_at + self.JANELA_DE_CORRECAO) - timezone.now()
        return max(int(restante.total_seconds()), 0)

    @property
    def pode_ser_corrigido_pela_loja(self):
        return self.segundos_para_corrigir > 0

    @property
    def recebido(self):
        """As formas de pagamento como o funcionario digitou.

        Nao e o que a loja vendeu: `dinheiro` e o que sobrou na gaveta, ja sem
        a retirada e sem as despesas. Quem responde "quanto a loja movimentou"
        e `total`.
        """
        return sum(getattr(self, campo) or 0 for campo in self.FORMAS_DE_PAGAMENTO)

    @property
    def total_das_despesas(self):
        """Soma das linhas de despesa do turno.

        Le da relacao ja carregada quando ha prefetch_related("despesas") — e
        ha em todo lugar que lista fechamento, senao o painel dispara uma query
        por turno so para fechar a conta.
        """
        return sum(
            (despesa.valor for despesa in self.despesas.all()), Decimal("0")
        )

    @property
    def registrado(self):
        """O que saiu da gaveta e ficou anotado a parte.

        Serve para a gerencia ler — quanto o dono levou, quanto foi gasto,
        quanto voltou para o cliente — e nao para descontar de nada. A
        devolucao entra aqui e NAO entra no total; sao perguntas diferentes.
        """
        return (
            (self.valor_retirado or 0)
            + self.total_das_despesas
            + (self.devolucao_valor or 0)
        )

    @property
    def total(self):
        """A liquidez bruta: quanto a loja movimentou no turno.

        `dinheiro` e o que esta FISICAMENTE na gaveta na hora de fechar — o
        funcionario conta o que tem na mao. Retirada e despesa ja sairam dali,
        entao voltam somando: o dinheiro saiu depois da venda, e a venda valeu.
        A retirada nem gasto e — o dono leva para nao deixar caixa grande no
        balcao, e o dinheiro continua sendo da empresa.

        A devolucao nao volta. Ela tambem saiu da gaveta, mas cancelou a venda
        junto: as duas coisas ja se anularam no numero contado. Somar faria a
        loja vender o que devolveu; subtrair faria ela vender 50 a menos do que
        vendeu. Neutra e a unica leitura que fecha.

        Ate 2026-09-01 esta conta SUBTRAIA retirada e despesa, tratando
        `dinheiro` como a venda bruta em especie. Descontava cada turno duas
        vezes — uma pela ausencia fisica, outra pela subtracao — e o erro era
        `2 x retirada + despesa`. Um turno de 1.200 com 500 retirados aparecia
        como 200.
        """
        return self.recebido + (self.valor_retirado or 0) + self.total_das_despesas

    # Nomes antigos, mantidos so ate o frontend subir com os novos. Sem eles a
    # tela por-loja (que le total_liquido) zera na janela entre um deploy e o
    # outro, que sao repositorios separados. Saem no PR de limpeza.

    @property
    def saidas(self):
        return self.registrado

    @property
    def total_liquido(self):
        return self.total

    def __str__(self):
        return f"Fechamento {self.loja.nome_loja} - {self.data} - {self.get_periodo_display()}"


class Despesa(BaseModel):
    """O que a loja gastou no turno, uma linha por gasto.

    Ja foi um par de campos dentro do FechamentoCaixa (`despesa_descricao` e
    `despesa_valor`), e por isso cabia uma despesa por turno. O expediente
    gasta com gas, agua e remedio no mesmo dia, e as outras iam empilhadas no
    campo de texto — onde nao somam, nao se separam, e a filha do dono nao
    consegue lancar cada uma na contabilidade dela.

    Sem cadastro por tras, ao contrario do consumo: "gas" nao e uma pessoa que
    precise sobreviver a uma correcao de grafia, e obrigar um cadastro faria a
    loja parar no meio do fechamento para criar "remedio".

    Pendurada no fechamento e nao solta com loja/data/turno proprios: foi
    lancada dentro do turno que estava sendo fechado, entao os tres campos
    seriam copia — e copia diverge no dia em que alguem corrigir o fechamento.

    SOMA no total do caixa. O dinheiro saiu da gaveta, mas a venda valeu: o
    numero que o funcionario contou ja esta sem ele, entao ele volta. Quem nao
    volta e a devolucao, que cancelou a venda junto.
    """

    fechamento = models.ForeignKey(
        FechamentoCaixa, on_delete=models.CASCADE, related_name="despesas"
    )
    descricao = models.CharField(max_length=200)
    valor = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["-fechamento__data", "descricao"]

    def __str__(self):
        return f"{self.descricao} - {self.fechamento.data} - {self.valor}"


class Consumo(BaseModel):
    """O que cada pessoa consumiu no turno, para a gerencia descontar depois.

    Ja foi um par de campos dentro do FechamentoCaixa (`houve_consumo` e
    `valor_consumo`), e por isso so cabia um por turno e so podia ser de quem
    lancou. Com 60 pessoas consumindo e 20 lancando o caixa, as outras 40 nao
    tinham onde aparecer — e e a soma dessas 60 que a dona desconta no fim do
    mes. Virou linha propria: o gerente lanca quantas precisar, uma por pessoa,
    no mesmo envio do fechamento.

    Pendurado no fechamento, e nao solto com loja/data/turno proprios: e o
    gerente que registra, dentro do turno que ele esta fechando, entao os tres
    campos seriam copia — e copia diverge no dia em que alguem corrigir o
    fechamento. Sai junto se o fechamento sair (CASCADE), porque foi o mesmo
    envio.

    Nao entra na conta do caixa: ninguem pagou na hora, nenhum dinheiro deixou
    a gaveta. Somar aqui faria o fechamento acusar uma diferenca que nao
    existe.
    """

    fechamento = models.ForeignKey(
        FechamentoCaixa, on_delete=models.CASCADE, related_name="consumos"
    )
    # PROTECT: apagar quem tem consumo lancado apagaria o valor do mes junto. A
    # tela desativa a pessoa, como ja faz com o responsavel de retirada.
    encarregado = models.ForeignKey(
        Encarregado, on_delete=models.PROTECT, related_name="consumos"
    )
    valor = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["-fechamento__data", "encarregado__nome"]

    def __str__(self):
        return f"{self.encarregado.nome} - {self.fechamento.data} - {self.valor}"


class DispositivoDoFormularioQuerySet(models.QuerySet):
    def ativo_por_token(self, token):
        """O aparelho vivo dono deste token, ou None.

        Compara pelo hash: o token cru so existe na resposta do acesso e no
        cookie do aparelho. Vazamento do banco nao entrega acesso a ninguem.
        """
        if not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        # A validade e checada aqui, e nao so no max_age do cookie: o max_age
        # e uma instrucao para o navegador, e quem copia o token de dentro de
        # um aparelho manda o valor na mao, sem cookie nenhum.
        nascidos_a_partir_de = timezone.now() - DispositivoDoFormulario.VALIDADE
        return (
            self.filter(
                token_hash=digest,
                revogado_em__isnull=True,
                conta__ativo=True,
                created_at__gte=nascidos_a_partir_de,
            )
            .select_related("conta")
            .first()
        )


class DispositivoDoFormulario(BaseModel):
    """Um celular/tablet que ja digitou o codigo da empresa.

    Existe para o acesso poder ser cortado por aparelho. Trocar o codigo
    derruba todo mundo — util quando o codigo vazou, brutal quando o problema
    e um unico celular que saiu da loja.
    """

    conta = models.ForeignKey(
        Conta, on_delete=models.CASCADE, related_name="dispositivos"
    )
    apelido = models.CharField(max_length=80)
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    ultimo_uso_em = models.DateTimeField(default=timezone.now)
    # Revogar e marcar, nao apagar: o painel precisa poder dizer "este aparelho
    # foi desconectado em tal dia" em vez de a linha simplesmente sumir.
    revogado_em = models.DateTimeField(null=True, blank=True)

    objects = DispositivoDoFormularioQuerySet.as_manager()

    # De quanto em quanto tempo o ultimo_uso_em e regravado. Sem isto, toda
    # chamada do formulario vira um UPDATE — o painel nao precisa de precisao
    # de segundo para dizer "usado hoje as 14h".
    INTERVALO_DE_USO = timedelta(hours=1)

    # Quanto tempo o aparelho vale sem digitar o codigo de novo. E a mesma
    # constante que vira o max_age do cookie, para os dois prazos nao
    # divergirem: o do navegador dizendo 180 dias e o do servidor, 30.
    VALIDADE = timedelta(days=180)

    class Meta:
        ordering = ["-ultimo_uso_em"]

    def registrar_uso(self):
        agora = timezone.now()
        if agora - self.ultimo_uso_em < self.INTERVALO_DE_USO:
            return
        self.ultimo_uso_em = agora
        self.save(update_fields=["ultimo_uso_em"])

    def __str__(self):
        return f"{self.apelido} - {self.conta.nome}"


class GrupoDeDespesa(BaseModel):
    """A categoria maior do gasto: Compras, Operacional, Pessoal, Impostos.

    Por conta e nao `choices` no codigo: cada empresa quebra o gasto do jeito
    dela, e um choices exigiria migration toda vez que alguem quisesse um
    subgrupo novo — o que na pratica significa que ninguem pediria.
    """

    conta = models.ForeignKey(
        Conta, on_delete=models.CASCADE, related_name="grupos_de_despesa"
    )
    nome = models.CharField(max_length=60)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["conta__nome", "nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["conta", "nome"], name="grupo_de_despesa_unico_por_conta"
            )
        ]

    def __str__(self):
        return self.nome


class ElementoDeDespesa(BaseModel):
    """O subgrupo especifico: Embalagem, Combustivel, Salario, Aluguel.

    Pendurado no grupo e nao com FK propria para Conta: a conta ja vem pelo
    grupo, e duas fontes para a mesma verdade divergem no dia em que alguem
    mover um elemento de grupo.
    """

    grupo = models.ForeignKey(
        GrupoDeDespesa, on_delete=models.CASCADE, related_name="elementos"
    )
    nome = models.CharField(max_length=60)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["grupo__nome", "nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["grupo", "nome"], name="elemento_unico_por_grupo"
            )
        ]

    @property
    def conta(self):
        return self.grupo.conta

    def __str__(self):
        return f"{self.grupo.nome} > {self.nome}"


class Fornecedor(BaseModel):
    """Quem emitiu a nota. Nasce sozinho na primeira nota daquele CNPJ.

    `elemento_sugerido` e a regra de "nao digitar duas vezes": toda vez que
    alguem classifica uma nota, o elemento escolhido fica gravado aqui, e a
    proxima nota do mesmo fornecedor ja chega com ele preenchido.

    Uma linha por CNPJ em cada conta: o mesmo fornecedor atende as 8 lojas, e
    duplicado por loja a sugestao nunca aprenderia.
    """

    conta = models.ForeignKey(
        Conta, on_delete=models.CASCADE, related_name="fornecedores"
    )
    cnpj = models.CharField(max_length=14)
    razao_social = models.CharField(max_length=200)
    # SET_NULL: apagar um elemento do plano de contas nao pode apagar o
    # fornecedor junto — ele so perde a sugestao.
    elemento_sugerido = models.ForeignKey(
        ElementoDeDespesa,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fornecedores_sugeridos",
    )

    class Meta:
        ordering = ["conta__nome", "razao_social"]
        constraints = [
            models.UniqueConstraint(
                fields=["conta", "cnpj"], name="fornecedor_unico_por_conta"
            )
        ]

    def __str__(self):
        return self.razao_social


class NotaFiscal(BaseModel):
    """Uma nota de entrada, lida do XML.

    E a metade do controle de despesas que entrega valor sem custo recorrente:
    a nota e a unica fonte que diz *o que* foi comprado, e a unica prova que
    existe quando o pagamento foi em dinheiro e o banco nunca viu.

    Nao confundir com `Despesa`, que e o gasto miudo tirado da gaveta durante o
    turno e vive pendurado no fechamento. Sao fluxos diferentes, responsaveis
    diferentes e ordens de grandeza diferentes; este modelo nao le aquele.
    """

    # Denormalizacao deliberada: a conta ja vem da loja, mas todo get_queryset
    # neste modulo filtra por conta; sem este campo seria uma join por loja em
    # cada listagem. Tradeoff: os dois podem divergir se a loja for movida entre
    # contas, o que nao ocorre hoje em nenhum caminho do sistema.
    conta = models.ForeignKey(
        Conta, on_delete=models.CASCADE, related_name="notas_fiscais"
    )
    # A loja sai do CNPJ do destinatario no XML, e nao de uma lista na tela:
    # ninguem erra o que nao digita. PROTECT porque apagar a loja levaria as
    # notas dela junto, e nota lancada e contabilidade.
    loja = models.ForeignKey(
        Loja, on_delete=models.PROTECT, related_name="notas_fiscais"
    )
    fornecedor = models.ForeignKey(
        Fornecedor, on_delete=models.PROTECT, related_name="notas"
    )

    # 44 digitos, unica no Brasil inteiro. E ela que impede a mesma nota entrar
    # duas vezes quando a pessoa sobe a pasta do e-mail de novo.
    chave = models.CharField(max_length=44, unique=True)
    numero = models.CharField(max_length=20)
    serie = models.CharField(max_length=10)
    data_emissao = models.DateField()
    valor_total = models.DecimalField(max_digits=12, decimal_places=2)

    # Nulo = ainda nao classificada, que e o estado em que toda nota nasce e a
    # fila de trabalho da tela. SET_NULL: mexer no plano de contas nao apaga
    # nota.
    elemento = models.ForeignKey(
        ElementoDeDespesa,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notas",
    )

    # O XML inteiro, como veio. Fica no banco e nao em arquivo porque o disco
    # do container e efemero — arquivo subido some no proximo deploy, ja que so
    # o volume do MySQL sobrevive a ele — e S3 seria
    # infra, credencial e custo novos antes de saber se o modulo serve. Os
    # ~100 notas/mes referem-se ao grupo inteiro em 8 lojas: 1200 notas/ano x
    # ~45 KB ≈ 54 MB/ano. Se a volumetria fosse 100 por loja, seria ~430 MB/ano.
    # Ambos os cenarios cabem no banco sem aperto.
    #
    # Guardado inteiro tambem para que os itens da nota possam ser extraidos
    # depois sem ninguem subir nada de novo.
    xml_bruto = models.TextField()

    enviada_por = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notas_enviadas",
    )

    class Meta:
        ordering = ["-data_emissao", "-created_at"]
        indexes = [
            # A tela filtra por loja e periodo, sempre dentro de uma conta.
            models.Index(fields=["conta", "data_emissao"]),
            models.Index(fields=["loja", "data_emissao"]),
        ]

    @property
    def classificada(self):
        return self.elemento_id is not None

    def __str__(self):
        return f"NF {self.numero} - {self.fornecedor.razao_social} - {self.valor_total}"


class CategoriaDeSalgado(BaseModel):
    """A familia do produto: Salgados grande, Esfihas mini, Fogazzas grande.

    Por conta e nao `choices` no codigo, pelo mesmo motivo do GrupoDeDespesa:
    cada empresa vende o que vende, e um choices exigiria migration toda vez
    que alguem quisesse uma familia nova.

    `ordem` existe porque a ordem que importa no formulario nao e a
    alfabetica: grande vem antes de mini, e "Esfihas" antes de "Fogazzas" e
    coincidencia do alfabeto, nao regra.
    """

    conta = models.ForeignKey(
        Conta, on_delete=models.CASCADE, related_name="categorias_de_salgado"
    )
    nome = models.CharField(max_length=60)
    ordem = models.PositiveIntegerField(default=0)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["ordem", "nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["conta", "nome"],
                name="categoria_de_salgado_unica_por_conta",
            )
        ]

    def __str__(self):
        return self.nome


class Salgado(BaseModel):
    """Um item do cardapio: a coxinha, o kibe, a esfiha de carne.

    Pendurado na categoria e nao com FK propria para Conta: a conta ja vem
    pela categoria, e duas fontes para a mesma verdade divergem no dia em que
    alguem mover um item de familia. Mesmo desenho do ElementoDeDespesa.

    Unico DENTRO da categoria e nao da conta, e isso NAO e detalhe: "Coxinha"
    existe em Salgados grande e em Salgados mini, "Calabresa" em tres
    categorias. Unicidade por (conta, nome) recusaria o catalogo real.

    O nome do modelo e impreciso ao pe da letra — esfiha e fogazza nao sao
    salgados — e fica assim por ser a palavra do negocio.
    """

    categoria = models.ForeignKey(
        CategoriaDeSalgado, on_delete=models.CASCADE, related_name="salgados"
    )
    nome = models.CharField(max_length=60)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["categoria__ordem", "categoria__nome", "nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["categoria", "nome"], name="salgado_unico_por_categoria"
            )
        ]

    @property
    def conta(self):
        return self.categoria.conta

    def __str__(self):
        return f"{self.categoria.nome} > {self.nome}"


class Desperdicio(BaseModel):
    """Quantos de cada item foram para o lixo no turno.

    Ja foi um par de campos dentro do FechamentoCaixa (`houve_desperdicio` e
    `desperdicio_detalhes`), e por isso era uma frase: "2 coxinhas e um quibe
    queimado" nao soma e nao se separa. Ninguem conseguia responder qual
    salgado a loja mais perde.

    Pendurado no fechamento e nao solto com loja/data/turno proprios: foi
    lancado dentro do turno que estava sendo fechado, entao os tres campos
    seriam copia — e copia diverge no dia em que alguem corrigir o fechamento.

    PROTECT no salgado: apagar o item apagaria o historico junto. A tela
    desativa, como ja se faz com o encarregado.

    Quantidade e nao valor: o catalogo nao tem preco nesta fase. Isso mantem o
    desperdicio FORA de qualquer soma em dinheiro — ninguem pagou nada, nenhum
    dinheiro deixou a gaveta.

    Duas linhas do mesmo item no mesmo turno sao permitidas, sem constraint,
    como no Consumo: recusar obrigaria a loja a somar de cabeca antes de
    digitar.
    """

    fechamento = models.ForeignKey(
        FechamentoCaixa, on_delete=models.CASCADE, related_name="desperdicios"
    )
    salgado = models.ForeignKey(
        Salgado, on_delete=models.PROTECT, related_name="desperdicios"
    )
    quantidade = models.PositiveIntegerField()

    class Meta:
        ordering = ["-fechamento__data", "salgado__nome"]

    def __str__(self):
        return f"{self.salgado.nome} x{self.quantidade} - {self.fechamento.data}"
