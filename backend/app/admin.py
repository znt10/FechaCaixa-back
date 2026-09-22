from django.contrib import admin

from .models import (
    CategoriaDeSalgado,
    Conta,
    Consumo,
    ElementoDeDespesa,
    Encarregado,
    FechamentoCaixa,
    Feriado,
    Fornecedor,
    GrupoDeDespesa,
    Desperdicio,
    Loja,
    NotaFiscal,
    PerfilUsuario,
    ResponsavelRetirada,
    Salgado,
)


# O LojaAdmin tinha um mixin que restringia o seletor de "gerente" ao grupo
# Gerente. O campo saiu da Loja: a gerente administra as lojas da empresa
# dela, e nao um subconjunto no nome dela.
@admin.register(Loja)
class LojaAdmin(admin.ModelAdmin):
    """A loja e de quem.

    Ficou registrada crua por muito tempo, e com uma empresa so ninguem sentia
    falta: o /admin mostrava a coluna do __str__ e pronto. Com duas empresas a
    lista virou "centro, Pirituba, Clipper" sem dizer de quem era cada uma.
    """

    list_display = ('nome_loja', 'conta', 'cnpj', 'cidade', 'ativo')
    list_filter = ('conta', 'ativo')
    search_fields = ('nome_loja', 'cidade', 'cnpj')
    ordering = ('conta__nome', 'nome_loja')
    list_select_related = ('conta',)
    autocomplete_fields = ('conta',)


# O /admin e a tela de correcao do fechamento: a API so aceita PATCH de
# Admin/Gerente e o funcionario nao tem login, entao quem corrige um
# lancamento errado passa por aqui.
@admin.register(Encarregado)
class EncarregadoAdmin(admin.ModelAdmin):
    """Quem toca a loja. Nao confundir com o grupo Funcionario, que e login."""

    list_display = ('nome', 'conta', 'pode_lancar_caixa', 'pode_consumir', 'ativo')
    list_filter = ('conta', 'pode_lancar_caixa', 'pode_consumir', 'ativo')
    # search_fields e o que faz o autocomplete do inline funcionar.
    search_fields = ('nome',)
    ordering = ('conta__nome', 'nome')
    list_select_related = ('conta',)
    autocomplete_fields = ('conta',)


@admin.register(FechamentoCaixa)
class FechamentoCaixaAdmin(admin.ModelAdmin):
    list_display = (
        'loja', 'empresa', 'data', 'periodo', 'nome_funcionario',
        'pix', 'cartao', 'dinheiro', 'link_pagamento', 'cancelado_em',
    )
    # A empresa vem primeiro no filtro por ser o corte mais largo: filtrar loja
    # a loja nao responde "me mostra a Primavera inteira", e com duas empresas
    # a propria lista de lojas do filtro ja vem misturada.
    list_filter = ('loja__conta', 'loja', 'periodo', 'data', 'cancelado_em')
    search_fields = ('nome_funcionario',)
    date_hierarchy = 'data'
    # Sem isso a coluna da empresa faz uma consulta por linha.
    list_select_related = ('loja', 'loja__conta')
    autocomplete_fields = ('responsavel_retirada', 'lancado_por')

    # O fechamento nao guarda conta de proposito: ela vem da loja, e duplicar o
    # campo criaria uma segunda verdade — mudar a loja de empresa deixaria os
    # lancamentos antigos apontando para a empresa velha.
    @admin.display(description='Empresa', ordering='loja__conta__nome')
    def empresa(self, fechamento):
        return fechamento.loja.conta

    def get_queryset(self, request):
        """O /admin ve inclusive o cancelado.

        `todos` explicito e nao acidente: o ModelAdmin ja cairia no
        _default_manager (que e "todos" — ver Meta.default_manager_name em
        FechamentoCaixa), mas deixar implicito faria esta tela depender de
        uma configuracao que mora em outro arquivo, sem nada aqui dizendo
        que e ela quem garante que o dono da plataforma nao perde de vista o
        cancelado.
        """
        return FechamentoCaixa.todos.select_related("loja__conta")


@admin.register(Consumo)
class ConsumoAdmin(admin.ModelAdmin):
    """O que cada pessoa consumiu. E aqui que se corrige um valor errado: o
    lancamento nao tem janela de correcao propria como o fechamento."""

    list_display = ('encarregado', 'empresa', 'loja', 'dia', 'valor')
    list_filter = ('encarregado__conta', 'fechamento__loja', 'fechamento__data')
    search_fields = ('encarregado__nome',)
    date_hierarchy = 'fechamento__data'
    list_select_related = (
        'encarregado', 'encarregado__conta', 'fechamento', 'fechamento__loja',
    )
    autocomplete_fields = ('encarregado',)

    @admin.display(description='Empresa', ordering='encarregado__conta__nome')
    def empresa(self, consumo):
        return consumo.encarregado.conta

    @admin.display(description='Loja', ordering='fechamento__loja__nome_loja')
    def loja(self, consumo):
        return consumo.fechamento.loja

    @admin.display(description='Dia', ordering='fechamento__data')
    def dia(self, consumo):
        return consumo.fechamento.data


@admin.register(ResponsavelRetirada)
class ResponsavelRetiradaAdmin(admin.ModelAdmin):
    # A conta aparece na linha e no filtro: o super admin ve a lista das
    # empresas todas junta, e "Marina" sozinha nao diz de quem e.
    list_display = ('nome', 'conta', 'ativo')
    list_filter = ('conta', 'ativo')
    search_fields = ('nome',)
    ordering = ('conta__nome', 'nome')
    list_select_related = ('conta',)
    autocomplete_fields = ('conta',)


# A conta e o vinculo do usuario com ela sao criados aqui pelo super admin —
# nao existe autoatendimento.
@admin.register(Conta)
class ContaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'slug', 'codigo_acesso', 'fechamentos_por_dia', 'ativo')
    # O slug vai no link do formulario; deixar editavel a mao quebraria o link
    # ja entregue as lojas.
    readonly_fields = ('slug',)
    search_fields = ('nome',)
    # O formulario sob medida num bloco proprio: sao seis campos que so fazem
    # sentido juntos, e no meio dos outros ninguem acharia "nome_dos_itens".
    # O primeiro bloco sai da lista que o Django monta sozinho, e nao de uma
    # lista escrita aqui: assim um campo novo em Conta continua aparecendo no
    # admin sem ninguem lembrar de acrescenta-lo.
    CAMPOS_DO_FORMULARIO = (
        'pergunta_retirada',
        'pergunta_despesa',
        'pergunta_devolucao',
        'pergunta_consumo',
        'catalogo_ativo',
        'nome_dos_itens',
    )

    def get_fieldsets(self, request, obj=None):
        todos = super().get_fieldsets(request, obj)[0][1]['fields']
        demais = [c for c in todos if c not in self.CAMPOS_DO_FORMULARIO]
        return (
            (None, {'fields': demais}),
            (
                'Formulário do caixa',
                {
                    'fields': self.CAMPOS_DO_FORMULARIO,
                    'description': (
                        'Quais perguntas opcionais a loja responde ao fechar o '
                        'caixa, e como a empresa chama o que vende. Desligar só '
                        'esconde a pergunta: o que já foi lançado continua lá.'
                    ),
                },
            ),
        )


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    list_display = ('user', 'conta')
    list_filter = ('conta',)
    autocomplete_fields = ('conta',)


@admin.register(Feriado)
class FeriadoAdmin(admin.ModelAdmin):
    """Feriados locais. Os nacionais nao entram aqui — sao calculados."""

    list_display = ('data', 'descricao', 'conta')
    list_filter = ('conta',)
    date_hierarchy = 'data'
    autocomplete_fields = ('conta',)


@admin.register(GrupoDeDespesa)
class GrupoDeDespesaAdmin(admin.ModelAdmin):
    list_display = ("nome", "conta", "ativo")
    list_filter = ("conta", "ativo")


@admin.register(ElementoDeDespesa)
class ElementoDeDespesaAdmin(admin.ModelAdmin):
    list_display = ("nome", "grupo", "ativo")
    list_filter = ("grupo__conta", "ativo")


@admin.register(Fornecedor)
class FornecedorAdmin(admin.ModelAdmin):
    list_display = ("razao_social", "cnpj", "conta", "elemento_sugerido")
    list_filter = ("conta",)
    search_fields = ("razao_social", "cnpj")


@admin.register(NotaFiscal)
class NotaFiscalAdmin(admin.ModelAdmin):
    list_display = (
        "numero", "fornecedor", "loja", "data_emissao", "valor_total", "elemento",
    )
    list_filter = ("conta", "loja", "elemento__grupo")
    search_fields = ("numero", "chave", "fornecedor__razao_social")
    date_hierarchy = "data_emissao"
    # O XML e prova, nao campo de digitacao: editavel, um deslize de teclado
    # apaga o unico original que a empresa tem.
    #
    # numero, serie, data_emissao e valor_total entram junto porque sao a
    # mesma prova lida do XML: gravaveis, o relatorio e o original voltam a
    # poder discordar em silencio, que e exatamente o que o modulo existe para
    # impedir — e a API ja os prende. conta e loja tambem, porque troca-los
    # aqui move a despesa de uma empresa para a outra por dentro do /admin.
    #
    # Que hoje so o dono da plataforma tenha is_staff nao dispensa a trava: a
    # prova nao pode depender de quem por acaso tem a senha.
    readonly_fields = (
        "chave",
        "xml_bruto",
        "numero",
        "serie",
        "data_emissao",
        "valor_total",
        "conta",
        "loja",
    )


# O catalogo de salgados, no mesmo molde do GrupoDeDespesa/ElementoDeDespesa:
# dois niveis porque o nome do item repete entre categorias — "Coxinha" existe
# em "Salgados grande" e em "Salgados mini". Por isso a categoria aparece em
# toda listagem de item aqui: sem ela duas linhas "Coxinha" ficariam
# indistinguiveis na tela.
@admin.register(CategoriaDeSalgado)
class CategoriaDeSalgadoAdmin(admin.ModelAdmin):
    list_display = ("nome", "conta", "ordem", "ativo")
    list_filter = ("conta", "ativo")
    # search_fields e o que faz o autocomplete de `categoria` no Salgado
    # funcionar.
    search_fields = ("nome",)
    ordering = ("conta__nome", "ordem", "nome")
    list_select_related = ("conta",)
    autocomplete_fields = ("conta",)


@admin.register(Salgado)
class SalgadoAdmin(admin.ModelAdmin):
    list_display = ("nome", "categoria", "empresa", "ativo")
    list_filter = ("categoria__conta", "ativo")
    # O nome sozinho nao identifica o item (ele repete entre categorias),
    # entao a busca alcanca tambem o nome da categoria — e assim que
    # "mini coxinha" acha o que se procura.
    search_fields = ("nome", "categoria__nome")
    ordering = ("categoria__conta__nome", "categoria__ordem", "nome")
    list_select_related = ("categoria", "categoria__conta")
    autocomplete_fields = ("categoria",)

    @admin.display(description="Empresa", ordering="categoria__conta__nome")
    def empresa(self, salgado):
        # `conta` no Salgado e propriedade (vem pela categoria), nao campo:
        # nao da para pedir direto no list_display.
        return salgado.categoria.conta


@admin.register(Desperdicio)
class DesperdicioAdmin(admin.ModelAdmin):
    """O que a loja perdeu no turno, uma linha por item. E aqui que se corrige
    uma quantidade errada: a linha nao tem janela de correcao propria como o
    fechamento."""

    list_display = ("salgado", "categoria", "empresa", "loja", "dia", "quantidade")
    list_filter = ("salgado__categoria__conta", "fechamento__loja", "fechamento__data")
    search_fields = ("salgado__nome",)
    date_hierarchy = "fechamento__data"
    list_select_related = (
        "salgado",
        "salgado__categoria",
        "salgado__categoria__conta",
        "fechamento",
        "fechamento__loja",
    )
    autocomplete_fields = ("salgado", "fechamento")

    @admin.display(description="Categoria", ordering="salgado__categoria__ordem")
    def categoria(self, desperdicio):
        return desperdicio.salgado.categoria

    @admin.display(description="Empresa", ordering="salgado__categoria__conta__nome")
    def empresa(self, desperdicio):
        return desperdicio.salgado.categoria.conta

    @admin.display(description="Loja", ordering="fechamento__loja__nome_loja")
    def loja(self, desperdicio):
        return desperdicio.fechamento.loja

    @admin.display(description="Dia", ordering="fechamento__data")
    def dia(self, desperdicio):
        return desperdicio.fechamento.data
