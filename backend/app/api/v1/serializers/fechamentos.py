"""Serializers do fechamento de caixa.

Leitura e escrita sao serializers diferentes de proposito: quem escreve e o
funcionario sem login (formulario publico) e quem le e a gerencia (painel).
O de escrita nao aceita nem devolve nada alem do que o formulario pede.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from app.feriados import eh_domingo, motivo_de_turno_unico
from app.models import (
    Consumo,
    Desperdicio,
    Despesa,
    Encarregado,
    FechamentoCaixa,
    Loja,
    ResponsavelRetirada,
    Retirada,
    Salgado,
)
from app.permissions import get_conta_do_usuario


class LojaPublicaSerializer(serializers.ModelSerializer):
    """So id e nome — o formulario e publico e cadastro de loja nao e.

    Existe porque o LojaSerializer devolve tambem cidade e endereco, que sao
    cadastro e nao tem por que sair sem login. (Ele ja devolveu email e
    responsavel; esses campos nem existem mais — sairam na migracao 0037.)
    """

    id = serializers.UUIDField(source="public_id", read_only=True)

    class Meta:
        model = Loja
        fields = ["id", "nome_loja"]


class ResponsavelRetiradaSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)

    class Meta:
        model = ResponsavelRetirada
        fields = ["id", "nome", "ativo"]


class EncarregadoSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)

    class Meta:
        model = Encarregado
        # As duas marcas saem na listagem e o formulario filtra: sao duas
        # listas da mesma gente (quem fecha o caixa, e quem entra na lista de
        # consumo), e dois endpoints para isso dariam duas respostas para
        # conferir.
        fields = ["id", "nome", "pode_lancar_caixa", "pode_consumir", "ativo"]


class DespesaDoTurnoSerializer(serializers.Serializer):
    """Uma linha de despesa dentro do envio do fechamento.

    Entra pelo formulario junto com o caixa do turno, como o consumo, e pelo
    mesmo motivo para nao ter endpoint proprio: dois caminhos de escrita para
    o mesmo dado deixariam a mesma despesa ser lancada duas vezes sem ninguem
    perceber.

    Sem cadastro por tras — "gas" e texto livre. Obrigar um cadastro faria a
    loja parar no meio do fechamento para criar "remedio".
    """

    descricao = serializers.CharField(max_length=200)
    valor = serializers.DecimalField(max_digits=10, decimal_places=2)

    def validate_valor(self, valor):
        if valor <= 0:
            raise serializers.ValidationError("Informe quanto foi a despesa.")
        return valor

    def validate(self, data):
        """Meia despesa nao existe.

        Num PATCH o DRF propaga `partial=True` para os serializers aninhados, e
        os dois campos viram opcionais — entao `{"descricao": "Gas"}` passaria
        e a linha entraria sem valor. Sem valor ela nao soma; sem descricao a
        contabilidade nao consegue lancar.
        """
        faltando = {"descricao", "valor"} - set(data)
        if faltando:
            raise serializers.ValidationError(
                "Informe a descricao e o valor da despesa."
            )
        return data


class DespesaLidaSerializer(serializers.ModelSerializer):
    """A mesma linha de volta, aninhada no fechamento."""

    id = serializers.UUIDField(source="public_id", read_only=True)

    class Meta:
        model = Despesa
        fields = ["id", "descricao", "valor"]
        read_only_fields = fields


class RetiradaDoTurnoSerializer(serializers.Serializer):
    """Uma linha de retirada dentro do envio do fechamento: quem levou, e quanto.

    Era um par de campos no fechamento, e cabia uma pessoa por turno. Entra
    pelo mesmo envio, como a despesa e o consumo, e pelo mesmo motivo para nao
    ter endpoint proprio.
    """

    responsavel = serializers.SlugRelatedField(
        slug_field="public_id",
        queryset=ResponsavelRetirada.objects.filter(ativo=True),
        error_messages={
            "does_not_exist": "Essa pessoa nao esta na lista de quem retira.",
        },
    )
    valor = serializers.DecimalField(max_digits=10, decimal_places=2)

    def validate_valor(self, valor):
        if valor <= 0:
            raise serializers.ValidationError("Informe quanto foi retirado.")
        return valor

    def validate(self, data):
        """Meia retirada nao existe — mesma razao da despesa: num PATCH o DRF
        propaga `partial=True` e os dois campos viram opcionais."""
        if {"responsavel", "valor"} - set(data):
            raise serializers.ValidationError(
                "Informe quem retirou e o valor retirado."
            )
        return data


class RetiradaLidaSerializer(serializers.ModelSerializer):
    """A mesma linha de volta, aninhada no fechamento, com o nome junto."""

    id = serializers.UUIDField(source="public_id", read_only=True)
    responsavel = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    # Nulo quando a pessoa foi apagada do cadastro: o valor continua somando.
    nome = serializers.CharField(source="responsavel.nome", read_only=True, default=None)

    class Meta:
        model = Retirada
        fields = ["id", "responsavel", "nome", "valor"]
        read_only_fields = fields


class ConsumoDoTurnoSerializer(serializers.Serializer):
    """Uma linha de consumo dentro do envio do fechamento.

    O consumo entra pelo formulario do gerente, junto com o caixa do turno:
    e ele quem sabe quem comeu o que. Nao existe endpoint proprio de escrita
    de proposito — dois caminhos para o mesmo dado deixariam o mesmo consumo
    ser lancado duas vezes sem ninguem perceber.
    """

    encarregado = serializers.SlugRelatedField(
        slug_field="public_id",
        queryset=Encarregado.objects.filter(ativo=True, pode_consumir=True),
        error_messages={
            "does_not_exist": "Essa pessoa nao esta na lista de consumo.",
        },
    )
    valor = serializers.DecimalField(max_digits=10, decimal_places=2)

    def validate_valor(self, valor):
        if valor <= 0:
            raise serializers.ValidationError("Informe quanto a pessoa consumiu.")
        return valor


class ConsumoLidoSerializer(serializers.ModelSerializer):
    """A mesma linha de volta, aninhada no fechamento.

    Vai junto do fechamento em vez de ter endpoint proprio porque o painel ja
    carrega o periodo inteiro de fechamentos: loja, dia e turno do consumo sao
    os do turno em que ele foi lancado, e buscar de novo so para reunir os
    mesmos campos seria uma segunda resposta para conferir com a primeira.
    """

    id = serializers.UUIDField(source="public_id", read_only=True)
    encarregado = serializers.SlugRelatedField(
        slug_field="public_id", read_only=True
    )
    nome = serializers.CharField(source="encarregado.nome", read_only=True)

    class Meta:
        model = Consumo
        fields = ["id", "encarregado", "nome", "valor"]
        read_only_fields = fields


class DesperdicioDoTurnoSerializer(serializers.Serializer):
    """Uma linha de desperdicio dentro do envio do fechamento.

    Nao existe endpoint proprio de escrita, pela mesma razao do consumo: dois
    caminhos para o mesmo dado deixariam a mesma perda ser lancada duas vezes
    sem ninguem perceber.

    O queryset ja filtra o inativo — do item E da categoria: desativar a
    familia "Fogazzas mini" tem que sumir com os sete itens dela do seletor,
    senao desativar categoria nao quer dizer nada.
    """

    salgado = serializers.SlugRelatedField(
        slug_field="public_id",
        queryset=Salgado.objects.filter(ativo=True, categoria__ativo=True),
        error_messages={
            "does_not_exist": "Esse item nao esta na lista de desperdicio.",
        },
    )
    quantidade = serializers.IntegerField()

    def validate_quantidade(self, quantidade):
        if quantidade <= 0:
            raise serializers.ValidationError("Informe quantos foram perdidos.")
        return quantidade


class DesperdicioLidoSerializer(serializers.ModelSerializer):
    """A mesma linha de volta, aninhada no fechamento.

    Vai junto do fechamento em vez de ter endpoint proprio porque o painel ja
    carrega o periodo inteiro: loja, dia e turno da perda sao os do turno em
    que ela foi lancada.
    """

    id = serializers.UUIDField(source="public_id", read_only=True)
    salgado = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    nome = serializers.CharField(source="salgado.nome", read_only=True)
    categoria_nome = serializers.CharField(
        source="salgado.categoria.nome", read_only=True
    )

    class Meta:
        model = Desperdicio
        fields = ["id", "salgado", "nome", "categoria_nome", "quantidade"]
        read_only_fields = fields


def gravar_despesas(fechamento, despesas):
    """Regrava as linhas de despesa de um turno.

    Fora das classes porque os dois serializers de escrita usam: o formulario
    da loja e a correcao da gerencia.
    """
    Despesa.objects.bulk_create(
        [
            Despesa(
                fechamento=fechamento,
                descricao=despesa["descricao"],
                valor=despesa["valor"],
            )
            for despesa in despesas
        ]
    )


def normalizar_retiradas(data, instance=None):
    """Traduz o que o payload disse da retirada para a lista de linhas.

    A lista `retiradas` e a verdade; `responsavel_retirada`/`valor_retirado`
    sao o resumo que `gravar_retiradas` escreve, e nunca vem do payload.
    Enquanto o app antigo estiver no ar ele manda o par, e o par vira uma
    linha — sem isto a loja perderia a retirada do turno em silencio entre um
    deploy e o outro.

    Deixa `data["retiradas"]` como None quando o payload nao falou de retirada
    (a correcao de PIX nao pode apagar a retirada do turno).
    """
    responsavel = data.pop("responsavel_retirada", None)
    valor = data.pop("valor_retirado", None)

    if data.get("retiradas") is not None:
        data["houve_retirada"] = bool(data["retiradas"])
        return
    data.pop("retiradas", None)

    if "houve_retirada" not in data:
        # Num PATCH sem a pergunta, o par sozinho ainda e uma correcao dele.
        if responsavel is None and valor is None:
            return
        data["houve_retirada"] = bool(instance and instance.houve_retirada)

    if not data["houve_retirada"]:
        data["retiradas"] = []
        return

    # Ligar a pergunta e nao preencher e o meio do caminho do formulario, nao
    # uma retirada.
    if not (responsavel and valor and valor > 0):
        raise serializers.ValidationError(
            {"valor_retirado": "Informe quem retirou e o valor retirado."}
        )
    data["retiradas"] = [{"responsavel": responsavel, "valor": valor}]


def validar_conta_das_retiradas(retiradas, conta_id):
    """O endpoint e publico: o payload pode citar gente da empresa vizinha."""
    for linha in retiradas or []:
        if conta_id and linha["responsavel"].conta_id != conta_id:
            raise serializers.ValidationError(
                {"retiradas": "Esse responsavel nao pertence a mesma conta da loja."}
            )


def gravar_retiradas(fechamento, retiradas):
    """Regrava as linhas de retirada de um turno e o resumo no fechamento.

    O resumo (soma, primeira pessoa) e escrito aqui e so aqui: e o que o total
    do caixa, os graficos e a planilha leem, e ele nao pode divergir das
    linhas.
    """
    fechamento.retiradas.all().delete()
    Retirada.objects.bulk_create(
        [
            Retirada(
                fechamento=fechamento,
                responsavel=linha["responsavel"],
                valor=linha["valor"],
            )
            for linha in retiradas
        ]
    )
    fechamento.houve_retirada = bool(retiradas)
    fechamento.responsavel_retirada = retiradas[0]["responsavel"] if retiradas else None
    fechamento.valor_retirado = (
        sum((linha["valor"] for linha in retiradas), Decimal("0")) if retiradas else None
    )
    fechamento.save(
        update_fields=["houve_retirada", "responsavel_retirada", "valor_retirado"]
    )


def gravar_consumos(fechamento, consumos):
    """Regrava as linhas de consumo de um turno.

    Fora das classes pela mesma razao do de despesas: o formulario da loja e a
    correcao da gerencia gravam a mesma lista.
    """
    Consumo.objects.bulk_create(
        [
            Consumo(
                fechamento=fechamento,
                encarregado=consumo["encarregado"],
                valor=consumo["valor"],
            )
            for consumo in consumos
        ]
    )


def gravar_desperdicios(fechamento, desperdicios):
    """Regrava as linhas de desperdicio de um turno.

    Fora das classes pela mesma razao das outras duas: o formulario da loja e
    a correcao da gerencia gravam a mesma lista.
    """
    Desperdicio.objects.bulk_create(
        [
            Desperdicio(
                fechamento=fechamento,
                salgado=linha["salgado"],
                quantidade=linha["quantidade"],
            )
            for linha in desperdicios
        ]
    )


class FechamentoCaixaSerializer(serializers.ModelSerializer):
    """Leitura — usada no painel do admin/gerente."""

    id = serializers.UUIDField(source="public_id", read_only=True)
    loja = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    loja_nome = serializers.CharField(source="loja.nome_loja", read_only=True)
    # O id junto com o nome: a tela de correcao precisa dele para deixar o
    # seletor de "quem retirou" ja marcado na pessoa certa, e casar pelo nome
    # daria errado no dia em que a conta tiver dois Bruno.
    responsavel_retirada = serializers.SlugRelatedField(
        slug_field="public_id", read_only=True
    )
    responsavel_retirada_nome = serializers.CharField(
        source="responsavel_retirada.nome", read_only=True, default=None
    )
    lancado_por = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    # Uma linha por pessoa. `responsavel_retirada`/`valor_retirado` acima sao o
    # resumo delas (a primeira pessoa e a soma), mantidos para quem ja le.
    retiradas = RetiradaLidaSerializer(many=True, read_only=True)
    despesas = DespesaLidaSerializer(many=True, read_only=True)
    consumos = ConsumoLidoSerializer(many=True, read_only=True)
    desperdicios = DesperdicioLidoSerializer(many=True, read_only=True)
    editado_por_nome = serializers.SerializerMethodField()
    conferido_por_nome = serializers.SerializerMethodField()
    # `recebido` sao as formas de pagamento cruas; `total` e a liquidez bruta,
    # que soma de volta o que saiu da gaveta depois da venda. Os dois numeros
    # respondem perguntas diferentes e a tela mostra os dois.
    recebido = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    registrado = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    # Obsoletos: iguais a registrado e total, mantidos ate o front subir.
    saidas = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    total_liquido = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )

    class Meta:
        model = FechamentoCaixa
        fields = [
            "id", "loja", "loja_nome", "nome_funcionario", "lancado_por",
            "data", "periodo",
            "pix", "cartao", "dinheiro", "link_pagamento",
            "recebido", "registrado", "total", "saidas", "total_liquido",
            "houve_retirada", "responsavel_retirada", "responsavel_retirada_nome",
            "valor_retirado", "retiradas",
            "despesas",
            "houve_devolucao", "devolucao_valor",
            "houve_desperdicio", "desperdicio_detalhes",
            "consumos",
            "desperdicios",
            "conferido", "conferido_em", "conferido_por_nome",
            "editado_por_nome",
            "created_at",
        ]

    def get_editado_por_nome(self, fechamento):
        return self._nome(fechamento.editado_por)

    def get_conferido_por_nome(self, fechamento):
        return self._nome(fechamento.conferido_por)

    @staticmethod
    def _nome(user):
        if not user:
            return None
        return user.first_name or user.email or user.username


class FechamentoCaixaUpdateSerializer(FechamentoCaixaSerializer):
    """Correcao pela gerencia — mesma leitura, mas com os valores editaveis.

    Herda do de leitura para que a resposta do PATCH seja igual a da listagem
    (o painel troca a linha pelo retorno, sem refetch).
    """

    responsavel_retirada = serializers.SlugRelatedField(
        slug_field="public_id",
        queryset=ResponsavelRetirada.objects.filter(ativo=True),
        required=False, allow_null=True,
    )
    # Gravavel aqui, so de leitura na listagem: corrigir o valor do gas e uma
    # das coisas que a gerencia mais faz depois que a loja manda.
    despesas = DespesaDoTurnoSerializer(many=True, required=False)
    # Quem levou dinheiro, uma linha por pessoa. Sem isto, corrigir um turno
    # com duas retiradas pelo par antigo apagaria a segunda.
    retiradas = RetiradaDoTurnoSerializer(many=True, required=False)
    # Pelo mesmo motivo, e por um mais caro: o consumo vira desconto no
    # pagamento da pessoa no fim do mes. Lancado na pessoa errada, so se
    # descobre la — e ate aqui o unico conserto era cancelar o turno inteiro e
    # relancar, jogando fora o caixa correto junto.
    consumos = ConsumoDoTurnoSerializer(many=True, required=False)

    class Meta(FechamentoCaixaSerializer.Meta):
        pass

    def to_representation(self, instance):
        """A resposta do PATCH continua identica a da listagem.

        Sem isto as despesas voltariam pelo serializer de escrita, sem o id — e
        o painel troca a linha pelo retorno, sem refetch.
        """
        dados = super().to_representation(instance)
        dados["retiradas"] = RetiradaLidaSerializer(
            instance.retiradas.select_related("responsavel"), many=True
        ).data
        dados["despesas"] = DespesaLidaSerializer(
            instance.despesas.all(), many=True
        ).data
        dados["consumos"] = ConsumoLidoSerializer(
            instance.consumos.select_related("encarregado"), many=True
        ).data
        return dados

    def update(self, instance, validated_data):
        retiradas = validated_data.pop("retiradas", None)
        despesas = validated_data.pop("despesas", None)
        consumos = validated_data.pop("consumos", None)
        with transaction.atomic():
            fechamento = super().update(instance, validated_data)
            if retiradas is not None:
                gravar_retiradas(fechamento, retiradas)
            # `None` e "o payload nao falou de despesa"; lista vazia e "nao
            # teve nenhuma", que apaga o que estava la.
            if despesas is not None:
                fechamento.despesas.all().delete()
                gravar_despesas(fechamento, despesas)
            # Mesma distincao para o consumo, e ela e o que impede uma correcao
            # de PIX de apagar o consumo do turno de tabela.
            if consumos is not None:
                fechamento.consumos.all().delete()
                gravar_consumos(fechamento, consumos)
        return fechamento

    def validate(self, data):
        """As mesmas tres regras do formulario, mas sobre o valor efetivo.

        Num PATCH parcial metade da dupla pode nao vir no payload: sem olhar
        tambem a instancia, apagar so o valor da despesa passaria e deixaria a
        linha afirmando que houve uma despesa de nada.
        """

        def efetivo(campo):
            return data[campo] if campo in data else getattr(self.instance, campo)

        # Sem o `efetivo`: a retirada chega inteira ou nao chega. O resumo no
        # fechamento nao e mais gravavel pelo payload.
        normalizar_retiradas(data, self.instance)
        if efetivo("houve_devolucao") and not efetivo("devolucao_valor"):
            raise serializers.ValidationError(
                {"devolucao_valor": "Informe o valor devolvido."}
            )
        if efetivo("houve_desperdicio") and not efetivo("desperdicio_detalhes"):
            raise serializers.ValidationError(
                {"desperdicio_detalhes": "Descreva o desperdicio."}
            )

        # Estar logado limita QUAIS turnos a gerencia alcanca, nao o que cabe
        # no corpo do PATCH: o queryset do campo aceita qualquer encarregado
        # ativo do sistema, entao o id da empresa vizinha entraria aqui. A loja
        # do turno e a instancia, e nao o payload — este serializer nao deixa
        # mudar de loja.
        conta_da_loja = self.instance.loja.conta_id
        validar_conta_das_retiradas(data.get("retiradas"), conta_da_loja)
        for consumo in data.get("consumos") or []:
            if consumo["encarregado"].conta_id != conta_da_loja:
                raise serializers.ValidationError(
                    {"consumos": "Essa pessoa nao e da mesma conta da loja."}
                )
        return data


class FechamentoCaixaCreateSerializer(serializers.ModelSerializer):
    """Escrita — usada no formulario publico do funcionario (sem login)."""

    loja = serializers.SlugRelatedField(
        slug_field="public_id", queryset=Loja.objects.filter(ativo=True)
    )
    responsavel_retirada = serializers.SlugRelatedField(
        slug_field="public_id",
        queryset=ResponsavelRetirada.objects.filter(ativo=True),
        required=False, allow_null=True,
    )
    # Quem esta lancando, escolhido na lista. Era digitado a mao, e por isso a
    # mesma pessoa aparecia escrita de tres formas — o que impedia somar o
    # consumo dela no mes.
    # Opcional no campo, obrigatorio na regra — e a regra sabe por qual porta o
    # lancamento chegou. Pelo aparelho quem lanca ESTA no turno, e escolher-se
    # na lista e o que faz o consumo do mes fechar por pessoa. Pelo painel nao
    # ha quem escolher: quem repoe um dia esquecido nao estava la, e `lancado_por`
    # aponta para Encarregado (gente da loja, sem login) enquanto o gerente e um
    # User — cadastros separados de proposito, sem FK que os ligue.
    lancado_por = serializers.SlugRelatedField(
        slug_field="public_id",
        queryset=Encarregado.objects.filter(ativo=True, pode_lancar_caixa=True),
        required=False,
        allow_null=True,
        error_messages={
            "does_not_exist": "Essa pessoa nao lanca o caixa.",
        },
    )
    # Quem levou dinheiro da gaveta. Uma linha por pessoa: o dono e a socia
    # retiram no mesmo turno, e antes cabia uma so. O par antigo acima ainda e
    # aceito enquanto o app das lojas nao sobe (ver `normalizar_retiradas`).
    retiradas = RetiradaDoTurnoSerializer(many=True, required=False)
    # O que a loja gastou no turno. Uma linha por gasto: gas, agua e remedio
    # sao tres despesas do mesmo expediente, e antes cabia uma so.
    despesas = DespesaDoTurnoSerializer(many=True, required=False)
    # Quem comeu o que, no turno que esta sendo fechado. Uma linha por pessoa:
    # o gerente lanca as que precisar, e a lista pode vir vazia.
    consumos = ConsumoDoTurnoSerializer(many=True, required=False)
    # O que a loja perdeu no turno, item a item do catalogo. Uma linha por
    # item: a mesma logica do consumo, uma lista que pode vir vazia.
    desperdicios = DesperdicioDoTurnoSerializer(many=True, required=False)

    # ---- formato antigo da despesa, aceito so durante a transicao ----
    # O formulario e um app separado, num deploy separado: entre o backend
    # subir e o frontend subir, as lojas continuam mandando a trinca antiga.
    # Sem isto elas perderiam a despesa do turno em silencio. Sai no PR de
    # limpeza, com o front ja no ar.
    houve_despesa = serializers.BooleanField(required=False, write_only=True)
    despesa_descricao = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, write_only=True
    )
    despesa_valor = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True,
        write_only=True,
    )

    class Meta:
        model = FechamentoCaixa
        fields = [
            "loja", "lancado_por", "data", "periodo",
            "pix", "cartao", "dinheiro", "link_pagamento",
            "houve_retirada", "responsavel_retirada", "valor_retirado",
            "retiradas",
            "despesas",
            "houve_devolucao", "devolucao_valor",
            "houve_desperdicio", "desperdicio_detalhes",
            "consumos",
            "desperdicios",
            "houve_despesa", "despesa_descricao", "despesa_valor",
        ]
        extra_kwargs = {"data": {"required": False}}

    # Ate quantos dias para tras o painel repoe um dia esquecido. Passado
    # isso o caixa daquele dia ja virou relatorio fechado, e mexer nele deixa
    # de ser "a loja esqueceu ontem" para virar reescrita de historico.
    DIAS_PARA_REPOR = 30

    def validate_data(self, value):
        # O endpoint e publico: sem isto, qualquer um lanca caixa de 2030 e
        # some do painel, que trabalha por dia.
        if value and value > timezone.localdate():
            raise serializers.ValidationError("A data nao pode estar no futuro.")

        # O piso e so do painel. O formulario da loja nunca escolhe data — ele
        # manda hoje — entao apertar os dois lados nao protegeria nada e
        # quebraria a suite, que lanca de datas fixas no passado de proposito.
        if value and self._pelo_painel(self.context.get("request")):
            limite = timezone.localdate() - timedelta(days=self.DIAS_PARA_REPOR)
            if value < limite:
                raise serializers.ValidationError(
                    f"So da para repor os ultimos {self.DIAS_PARA_REPOR} dias."
                )
        return value

    def validate(self, data):
        self._aceitar_a_despesa_antiga(data)

        # Quem lanca pela loja continua tendo que se identificar: o cadastro
        # existe para acabar com "Marina", "marina" e "Mari" sendo tres
        # pessoas na hora de somar o consumo do mes. So a reposicao pelo painel
        # manda sem ninguem.
        if (
            self.instance is None
            and not data.get("lancado_por")
            and not self._pelo_painel(self.context.get("request"))
        ):
            raise serializers.ValidationError(
                {"lancado_por": "Escolha quem esta lancando o caixa."}
            )

        normalizar_retiradas(data, self.instance)
        if data.get("houve_devolucao") and not data.get("devolucao_valor"):
            raise serializers.ValidationError(
                {"devolucao_valor": "Informe o valor devolvido."}
            )
        if data.get("houve_desperdicio") and not data.get("desperdicio_detalhes"):
            raise serializers.ValidationError(
                {"desperdicio_detalhes": "Descreva o desperdicio."}
            )

        # Domingo e feriado a loja abre mais tarde e fecha mais tarde: e um
        # expediente so, que nao e a manha nem a tarde. Sem isto o formulario
        # deixaria escolher um turno que nunca aconteceu, e o painel cobraria
        # um fechamento que ninguem devia.
        #
        # Sao dois turnos diferentes: domingo tem o seu (toda semana, e a
        # gerencia compara um com o outro) e feriado cai no do dia inteiro.
        # Numa correcao o payload pode nao repetir data e loja: sem o fallback
        # para a instancia, a regra de domingo/feriado olharia o dia de hoje.
        dia = data.get("data") or (self.instance.data if self.instance else None)
        dia = dia or timezone.localdate()
        loja = data.get("loja") or (self.instance.loja if self.instance else None)

        # O endpoint e publico e o payload carrega o public_id da loja: sem
        # esta checagem, nada impede colar ali a loja de outra empresa. A
        # conta de referencia e a do aparelho (request.conta_do_formulario),
        # nunca a que o proprio payload alega.
        request = self.context.get("request")
        conta_do_formulario = getattr(request, "conta_do_formulario", None)
        if loja and conta_do_formulario and loja.conta_id != conta_do_formulario.id:
            raise serializers.ValidationError(
                {"loja": "Esta loja nao e da sua empresa."}
            )

        # Pelo painel nao existe cookie de aparelho: a conta de referencia e a
        # do login. Sem isto o gerente lancava caixa na empresa vizinha — as
        # demais checagens deste metodo so comparam as pecas do payload UMA
        # COM A OUTRA, e um payload inteiro da vizinha (loja + encarregado
        # dela) passa por todas elas sem esbarrar em nada.
        conta_do_login = self._conta_do_login(request)
        if loja and conta_do_login and loja.conta_id != conta_do_login.id:
            raise serializers.ValidationError(
                {"loja": "Esta loja nao e da sua empresa."}
            )

        # Quantos fechamentos a empresa faz por dia decide quais periodos
        # existem para ela. A conta de referencia e a do aparelho quando quem
        # lanca e a loja; no painel, a da propria loja do lancamento.
        conta = conta_do_formulario or (loja.conta if loja else None)
        periodo = data.get("periodo") or (
            self.instance.periodo if self.instance else None
        )
        um_por_dia = bool(conta and conta.fechamentos_por_dia == 1)

        if um_por_dia and periodo != FechamentoCaixa.Periodo.DIA:
            raise serializers.ValidationError(
                {"periodo": "Esta empresa faz um fechamento por dia."}
            )

        # A regra de domingo/feriado fala de manha e tarde: ela tira as duas e
        # poe uma no lugar. Numa empresa que so tem "DIA" nao ha nada para
        # tirar — e perguntar de novo proibiria o unico fechamento que ela faz.
        if not um_por_dia:
            motivo = motivo_de_turno_unico(dia, loja.conta if loja else None)
            esperado = (
                FechamentoCaixa.Periodo.DOMINGO
                if eh_domingo(dia)
                else FechamentoCaixa.Periodo.DIA
            )
            if motivo and periodo != esperado:
                raise serializers.ValidationError(
                    {"periodo": f"{motivo}: a loja abre num turno so."}
                )
            # Sem saber a conta nao da para recusar "DIA": ela pode ser das que
            # fecham o caixa uma vez so, e a recusa seria contra a propria
            # empresa. "DOMINGO" fora de domingo nao tem essa duvida — nenhuma
            # empresa lanca domingo numa terca.
            if not motivo and (
                periodo == FechamentoCaixa.Periodo.DOMINGO
                or (conta and periodo == FechamentoCaixa.Periodo.DIA)
            ):
                raise serializers.ValidationError(
                    {"periodo": "Escolha manha ou tarde."}
                )

        # Loja e responsavel tem que ser da mesma conta: o endpoint e publico,
        # entao nada impede alguem de colar no payload o public_id de uma loja
        # de outra conta.
        validar_conta_das_retiradas(data.get("retiradas"), loja.conta_id if loja else None)

        # Mesma razao para quem lancou e para quem consumiu: o endpoint e
        # publico, entao o payload pode citar gente da empresa vizinha.
        lancado_por = data.get("lancado_por")
        if loja and lancado_por and lancado_por.conta_id != loja.conta_id:
            raise serializers.ValidationError(
                {"lancado_por": "Essa pessoa nao e da mesma conta da loja."}
            )

        for consumo in data.get("consumos") or []:
            if loja and consumo["encarregado"].conta_id != loja.conta_id:
                raise serializers.ValidationError(
                    {"consumos": "Essa pessoa nao e da mesma conta da loja."}
                )

        # Mesma razao do consumo: o endpoint e publico, entao o payload pode
        # citar o item do catalogo da empresa vizinha.
        for linha in data.get("desperdicios") or []:
            if loja and linha["salgado"].categoria.conta_id != loja.conta_id:
                raise serializers.ValidationError(
                    {"desperdicios": "Esse item nao e do catalogo da sua empresa."}
                )

        # Repor pressupoe buraco. Onde ja existe caixa lancado, um segundo
        # envio nao corrige o primeiro: os dois ficam, e o painel soma os dois
        # como se a loja tivesse vendido o dobro.
        #
        # So do lado do painel. O formulario da loja continua aceitando —
        # apertar os dois lados mudaria o comportamento de quem ja usa, e nao
        # e o buraco que esta tela veio tapar.
        #
        # `objects` esconde os cancelados de proposito: cancelar existe para o
        # turno voltar a aceitar envio, e olhar tambem os desfeitos deixaria o
        # dia impossivel de refazer.
        if (
            self.instance is None
            and self._pelo_painel(request)
            and loja
            and periodo
            and FechamentoCaixa.objects.filter(
                loja=loja, data=dia, periodo=periodo
            ).exists()
        ):
            raise serializers.ValidationError(
                {"periodo": "Este turno ja tem fechamento lancado."}
            )

        return data

    @staticmethod
    def _pelo_painel(request):
        """O lancamento chegou pela tela da gerencia, e nao pelo aparelho.

        Separado de `_conta_do_login` porque as duas perguntas divergem num
        caso: o superusuario da plataforma esta no painel (e pega as travas
        de la) sem pertencer a conta nenhuma.
        """
        user = getattr(request, "user", None)
        return bool(user is not None and user.is_authenticated)

    @staticmethod
    def _conta_do_login(request):
        """A conta de quem esta logado — None quando quem lanca e a loja.

        O superusuario da plataforma fica de fora de proposito: ele enxerga
        todas as contas no resto do projeto (ver `escopo_de_lojas`), e prende-lo
        a uma so aqui seria a unica excecao a essa regra.
        """
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or user.is_superuser:
            return None
        return get_conta_do_usuario(user)

    @staticmethod
    def _aceitar_a_despesa_antiga(data):
        """Converte a trinca do formulario antigo numa linha de despesa.

        So vale enquanto o app das lojas nao subir com o formato novo. Se as
        duas formas vierem, a lista nova ganha: a trinca e o rastro do app
        antigo, nao uma segunda despesa.
        """
        houve = data.pop("houve_despesa", None)
        descricao = data.pop("despesa_descricao", None)
        valor = data.pop("despesa_valor", None)

        if data.get("despesas") is not None:
            return
        if not houve:
            return
        # Ligar a pergunta e nao preencher continua sendo recusado, como era
        # quando a despesa morava no fechamento: aceitar em silencio perderia a
        # despesa do turno em vez de mandar a loja completar.
        if not valor or valor <= 0:
            raise serializers.ValidationError(
                {"despesa_valor": "Informe a descricao e o valor da despesa."}
            )
        data["despesas"] = [
            {"descricao": (descricao or "").strip() or "Despesa", "valor": valor}
        ]

    def create(self, validated_data):
        retiradas = validated_data.pop("retiradas", None) or []
        despesas = validated_data.pop("despesas", [])
        consumos = validated_data.pop("consumos", [])
        desperdicios = validated_data.pop("desperdicios", [])
        validated_data.setdefault("data", timezone.localdate())
        self._copiar_o_nome(validated_data)
        # Um envio so: o turno com consumo de meia loja nao pode gravar pela
        # metade e deixar a gerencia descontando de uns e nao de outros.
        with transaction.atomic():
            fechamento = super().create(validated_data)
            gravar_retiradas(fechamento, retiradas)
            gravar_despesas(fechamento, despesas)
            gravar_consumos(fechamento, consumos)
            gravar_desperdicios(fechamento, desperdicios)
        return fechamento

    def update(self, instance, validated_data):
        """A correcao da loja, dentro dos 20 minutos, passa por aqui.

        Existe por causa do nome: trocar quem lancou deixaria
        `nome_funcionario` com o nome antigo, e e esse texto que sete telas
        leem.
        """
        retiradas = validated_data.pop("retiradas", None)
        despesas = validated_data.pop("despesas", None)
        consumos = validated_data.pop("consumos", None)
        desperdicios = validated_data.pop("desperdicios", None)
        self._copiar_o_nome(validated_data)
        with transaction.atomic():
            fechamento = super().update(instance, validated_data)
            if retiradas is not None:
                gravar_retiradas(fechamento, retiradas)
            if despesas is not None:
                fechamento.despesas.all().delete()
                gravar_despesas(fechamento, despesas)
            # `None` e "o payload nao falou de consumo"; lista vazia e "nao
            # teve nenhum", que apaga o que estava la. Sem essa diferenca, uma
            # correcao de PIX apagaria o consumo do turno de tabela.
            if consumos is not None:
                fechamento.consumos.all().delete()
                gravar_consumos(fechamento, consumos)
            # Mesma distincao para o desperdicio: `None` e "o payload nao
            # falou de desperdicio"; lista vazia e "nao teve nenhum", que
            # apaga o que estava la. Sem ela, corrigir o PIX apagaria o
            # desperdicio do turno de tabela.
            if desperdicios is not None:
                fechamento.desperdicios.all().delete()
                gravar_desperdicios(fechamento, desperdicios)
        return fechamento

    # O que marca um dia reposto pelo painel. Vai junto do nome porque um dia
    # reposto que nao se distingue de um que a loja mandou e exatamente o que a
    # gerencia nao pode ter na hora de auditar.
    MARCA_DE_REPOSICAO = " - reposto pelo painel"

    def _copiar_o_nome(self, validated_data):
        """O texto continua gravado ao lado da FK.

        Sete telas leem `nome_funcionario`, e os lancamentos anteriores ao
        cadastro so tem ele. Copiado aqui para nunca divergir da pessoa
        escolhida.

        Na reposicao pelo painel nao ha FK para copiar — quem repos nao estava
        no turno, e nem sequer e um Encarregado. Entao entra aqui o nome de
        quem repos, com a marca: sem isso o dia reposto fica em branco onde
        todos os outros dizem quem fechou o caixa.
        """
        pessoa = validated_data.get("lancado_por")
        if pessoa:
            validated_data["nome_funcionario"] = pessoa.nome
            return

        user = getattr(self.context.get("request"), "user", None)
        if user is None or not user.is_authenticated:
            return

        nome = user.first_name or user.email or user.username
        # A coluna tem 100 caracteres. Login de e-mail comprido mais a marca
        # estourava a coluna, e no MySQL isso sobe como erro de banco — 500 na
        # cara de quem so queria repor uma terca.
        cabe = 100 - len(self.MARCA_DE_REPOSICAO)
        validated_data["nome_funcionario"] = f"{nome[:cabe]}{self.MARCA_DE_REPOSICAO}"

    def to_representation(self, instance):
        # O funcionario nao pode listar fechamentos, mas precisa ver o que
        # acabou de mandar (tela de confirmacao) — devolvemos so isso.
        return {
            "id": str(instance.public_id),
            "loja_nome": instance.loja.nome_loja,
            "data": instance.data,
            "periodo": instance.periodo,
            "total": instance.total,
            "registrado": instance.registrado,
            "despesas": DespesaLidaSerializer(instance.despesas, many=True).data,
            # A confirmacao repete o consumo lancado: e o unico momento em que
            # o gerente ve de volta o que digitou de cada pessoa, e e ele quem
            # responde por esse numero no fim do mes.
            "consumos": ConsumoLidoSerializer(
                instance.consumos.select_related("encarregado"), many=True
            ).data,
            # O prazo vem em segundos, ja calculado aqui: mandar o horario
            # limite obrigaria o celular da loja a ter a hora certa, e ele
            # frequentemente nao tem.
            "segundos_para_corrigir": instance.segundos_para_corrigir,
        }


class FechamentoCaixaFormularioSerializer(serializers.ModelSerializer):
    """O lancamento de volta no formato do formulario.

    Existe porque a loja pode fechar a aba e reabrir o link dentro dos 20
    minutos: o formulario precisa se preencher de novo a partir do servidor,
    e nao do que sobrou na memoria do navegador. So os campos que o
    funcionario digitou — nada de conferido, editado_por ou dados da conta.
    """

    id = serializers.UUIDField(source="public_id", read_only=True)
    loja = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    loja_nome = serializers.CharField(source="loja.nome_loja", read_only=True)
    responsavel_retirada = serializers.SlugRelatedField(
        slug_field="public_id", read_only=True
    )
    retiradas = RetiradaLidaSerializer(many=True, read_only=True)
    despesas = DespesaLidaSerializer(many=True, read_only=True)
    segundos_para_corrigir = serializers.IntegerField(read_only=True)
    # Declarado: sem isto o ModelSerializer devolveria a chave inteira em
    # lancado_por.
    lancado_por = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    consumos = ConsumoLidoSerializer(many=True, read_only=True)
    desperdicios = DesperdicioLidoSerializer(many=True, read_only=True)

    class Meta:
        model = FechamentoCaixa
        fields = [
            "id", "loja", "loja_nome", "nome_funcionario", "lancado_por",
            "data", "periodo",
            "pix", "cartao", "dinheiro", "link_pagamento",
            "houve_retirada", "responsavel_retirada", "valor_retirado",
            "retiradas",
            "despesas",
            "houve_devolucao", "devolucao_valor",
            "houve_desperdicio", "desperdicio_detalhes",
            "consumos",
            "desperdicios",
            "segundos_para_corrigir",
        ]
        read_only_fields = fields
