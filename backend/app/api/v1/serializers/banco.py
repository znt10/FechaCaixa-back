from rest_framework import serializers

from app.models import (
    CategoriaDeMovimento,
    ContaBancaria,
    ElementoDeDespesa,
    Loja,
    TipoDeMovimento,
    TransacaoBancaria,
)

from .notas import ElementoDeDespesaSerializer, ElementoPorIdPublico, LojaDaNotaSerializer

# O mesmo campo das notas, que nada tem de especifico do elemento: um id que
# nem e UUID vira "nao existe", e nao a frase do Django com o id colado dentro.
PorIdPublico = ElementoPorIdPublico


class ContaBancariaSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    loja = LojaDaNotaSerializer(read_only=True)
    banco_nome = serializers.CharField(source="get_banco_display", read_only=True)

    # Escrita por id, leitura aninhada, pelo mesmo motivo do `grupo_id` do
    # elemento. O queryset e o global: quem responde "essa loja e sua" e o
    # ViewSet, onde o usuario do pedido esta.
    loja_id = PorIdPublico(
        source="loja",
        slug_field="public_id",
        queryset=Loja.objects.all(),
        write_only=True,
        error_messages={
            "required": "Escolha a loja desta conta.",
            "null": "Escolha a loja desta conta.",
            "does_not_exist": "A loja escolhida não existe.",
            "invalid": "A loja escolhida não existe.",
        },
    )

    class Meta:
        model = ContaBancaria
        fields = [
            "id", "loja", "loja_id", "banco", "banco_nome", "agencia", "numero",
            "apelido", "ativo",
        ]
        # Sem o validador de unicidade que o DRF monta sozinho a partir da
        # restricao do modelo: ele tornava a agencia obrigatoria (o Nubank e o
        # PicPay nem mostram agencia) e respondia "devem criar um set unico".
        # Quem recusa a conta repetida, com frase de gente, e o ViewSet.
        validators = []


class CategoriaDeMovimentoSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    tipo_nome = serializers.CharField(source="get_tipo_display", read_only=True)

    class Meta:
        model = CategoriaDeMovimento
        fields = ["id", "tipo", "tipo_nome", "nome", "ativo", "entre_lojas"]
        read_only_fields = ["entre_lojas"]


class ContaBancariaDaTransacaoSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    loja = LojaDaNotaSerializer(read_only=True)
    banco_nome = serializers.CharField(source="get_banco_display", read_only=True)

    class Meta:
        model = ContaBancaria
        fields = ["id", "loja", "banco", "banco_nome", "numero", "apelido"]


class CategoriaDaTransacaoSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)

    class Meta:
        model = CategoriaDeMovimento
        fields = ["id", "nome", "tipo", "entre_lojas"]


class ParDaTransacaoSerializer(serializers.ModelSerializer):
    """A outra ponta da transferencia, so o bastante para a tela dizer
    "veio da Loja B"."""

    id = serializers.UUIDField(source="public_id", read_only=True)
    conta_bancaria = ContaBancariaDaTransacaoSerializer(read_only=True)

    class Meta:
        model = TransacaoBancaria
        fields = ["id", "data", "conta_bancaria"]


class TransacaoBancariaSerializer(serializers.ModelSerializer):
    """A transacao como a tela a mostra: tudo aninhado, pelo mesmo motivo da
    nota — a tabela mostra loja, conta e categoria em toda linha."""

    id = serializers.UUIDField(source="public_id", read_only=True)
    conta_bancaria = ContaBancariaDaTransacaoSerializer(read_only=True)
    tipo_nome = serializers.CharField(source="get_tipo_display", read_only=True)
    elemento = ElementoDeDespesaSerializer(read_only=True)
    categoria = CategoriaDaTransacaoSerializer(read_only=True)
    par = ParDaTransacaoSerializer(read_only=True)
    classificada = serializers.BooleanField(read_only=True)

    class Meta:
        model = TransacaoBancaria
        fields = [
            "id", "data", "valor", "descricao", "conta_bancaria", "tipo",
            "tipo_nome", "elemento", "categoria", "par", "classificada",
        ]
        read_only_fields = fields


class ClassificacaoDaTransacaoSerializer(serializers.Serializer):
    """O corpo do PATCH: so a classificacao, nunca a transacao.

    Data, valor e descricao vieram do banco e ficam presos, pelo mesmo motivo
    dos campos da nota: o arquivo original esta guardado, e deixa-los
    gravaveis faria a tela e o extrato discordarem em silencio.

    Os querysets sao globais de proposito, como no serializer da nota: eles
    dizem "existe", e quem diz "e da sua empresa" e o ViewSet.
    """

    tipo = serializers.ChoiceField(
        choices=TipoDeMovimento.choices,
        allow_null=True,
        error_messages={
            "required": "Escolha o tipo.",
            "invalid_choice": "Tipo inválido.",
        },
    )
    elemento = PorIdPublico(
        slug_field="public_id",
        queryset=ElementoDeDespesa.objects.select_related("grupo"),
        allow_null=True,
        required=False,
        error_messages={
            "does_not_exist": "O elemento de despesa escolhido não existe.",
            "invalid": "O elemento de despesa escolhido não existe.",
        },
    )
    categoria = PorIdPublico(
        slug_field="public_id",
        queryset=CategoriaDeMovimento.objects.all(),
        allow_null=True,
        required=False,
        error_messages={
            "does_not_exist": "A categoria escolhida não existe.",
            "invalid": "A categoria escolhida não existe.",
        },
    )
    aplicar_as_iguais = serializers.BooleanField(default=False)
