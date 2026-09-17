from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import serializers

from app.models import ElementoDeDespesa, Fornecedor, GrupoDeDespesa, Loja, NotaFiscal


class ElementoPorIdPublico(serializers.SlugRelatedField):
    """O elemento escolhido, procurado pelo public_id que a API expoe.

    Existe so por causa da frase do erro: um id que nem sequer e um UUID faz
    o Django levantar ValidationError na conversao, e a mensagem dela ("O
    valor ... nao e um UUID valido", com o id colado dentro) vaza para a tela
    da gerencia da padaria. Para quem usa a tela, um id que nao casa com nada
    e um elemento que nao existe — nao um problema de formato.
    """

    def to_internal_value(self, dados):
        try:
            return super().to_internal_value(dados)
        except DjangoValidationError:
            self.fail("does_not_exist", slug_name=self.slug_field, value=dados)


class LojaDaNotaSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)

    class Meta:
        model = Loja
        fields = ["id", "nome_loja"]


class FornecedorSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)

    class Meta:
        model = Fornecedor
        fields = ["id", "razao_social", "cnpj"]


class ElementoDeDespesaSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    grupo = serializers.CharField(source="grupo.nome", read_only=True)

    # A leitura continua sendo o nome do grupo (`grupo`), porque e o que a
    # tela mostra na lista; a escrita e por id e tem nome proprio, ja que dois
    # campos com o mesmo nome nao existem. Mesmo desenho do `elemento_id` da
    # nota, e por `public_id`: a API nunca expoe a chave do banco.
    #
    # O queryset e o global de proposito — ele so responde "existe um grupo
    # com esse id". Quem responde "esse grupo e seu" e o perform_create do
    # ViewSet, que e onde o usuario do pedido esta.
    grupo_id = serializers.SlugRelatedField(
        source="grupo",
        slug_field="public_id",
        queryset=GrupoDeDespesa.objects.all(),
        write_only=True,
        error_messages={
            # Sem termo tecnico: a frase aparece na tela da gerencia da
            # padaria, que nao tem por que saber o que e um campo obrigatorio
            # ou uma chave que nao existe.
            "required": "Escolha o grupo de despesa deste elemento.",
            "null": "Escolha o grupo de despesa deste elemento.",
            "does_not_exist": "O grupo de despesa escolhido nao existe.",
            "invalid": "O grupo de despesa escolhido nao existe.",
        },
    )

    class Meta:
        model = ElementoDeDespesa
        fields = ["id", "nome", "grupo", "grupo_id", "ativo"]


class GrupoDeDespesaSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    elementos = ElementoDeDespesaSerializer(many=True, read_only=True)

    class Meta:
        model = GrupoDeDespesa
        fields = ["id", "nome", "ativo", "elementos"]


class NotaFiscalSerializer(serializers.ModelSerializer):
    """A nota como a tela a mostra.

    Loja, fornecedor e elemento saem aninhados e nao como id: a tabela mostra
    os tres em toda linha, e devolver so o id obrigaria o front a buscar e
    casar tres listas para desenhar uma pagina.
    """

    id = serializers.UUIDField(source="public_id", read_only=True)
    loja = LojaDaNotaSerializer(read_only=True)
    fornecedor = FornecedorSerializer(read_only=True)
    elemento = ElementoDeDespesaSerializer(read_only=True)
    classificada = serializers.BooleanField(read_only=True)

    # A escrita e por id; a leitura sai aninhada. Dois campos com o mesmo nome
    # nao existem, entao o de escrita e write_only e tem nome proprio. Quem
    # traduz `elemento` (o nome que a API publica aceita no PATCH) para
    # `elemento_id` e o NotaFiscalViewSet.
    #
    # O queryset e o global de proposito: ele so diz "existe um elemento com
    # esse id", nao "esse elemento e seu". Filtrar por conta AQUI daria 400
    # com a mensagem errada e, pior, nao valeria para o superuser. Quem recusa
    # o elemento da empresa vizinha e o perform_update do ViewSet, que e onde
    # o usuario do pedido esta.
    elemento_id = ElementoPorIdPublico(
        source="elemento",
        slug_field="public_id",
        queryset=ElementoDeDespesa.objects.all(),
        write_only=True,
        allow_null=True,
        required=False,
        error_messages={
            # Sem termo tecnico e sem o valor recebido: a frase aparece na
            # tela da gerencia da padaria, que nao tem por que ler "UUID" nem
            # o id que o front mandou.
            "does_not_exist": "O elemento de despesa escolhido nao existe.",
            "invalid": "O elemento de despesa escolhido nao existe.",
        },
    )

    def to_internal_value(self, dados):
        """Reporta o erro no nome que a API publica usa: `elemento`.

        O campo gravavel se chama `elemento_id` so por dentro, ja que dois
        campos com o mesmo nome nao existem. Deixar esse nome sair na resposta
        entrega detalhe de implementacao para uma tela que so conhece
        `elemento` — e manda a gerente procurar na tela um campo que nao esta
        escrito em lugar nenhum.
        """
        try:
            return super().to_internal_value(dados)
        except serializers.ValidationError as erro:
            detalhe = erro.detail
            if isinstance(detalhe, dict) and "elemento_id" in detalhe:
                detalhe["elemento"] = detalhe.pop("elemento_id")
            raise serializers.ValidationError(detalhe)

    class Meta:
        model = NotaFiscal
        fields = [
            "id", "numero", "serie", "data_emissao", "valor_total",
            "loja", "fornecedor", "elemento", "elemento_id", "classificada",
        ]
        # `elemento_id` e o UNICO campo gravavel deste serializer: todo o
        # resto e read_only, aqui embaixo ou la em cima. E por isso que o
        # ViewSet volta a expor UpdateModelMixin sem reabrir a nota inteira.
        #
        # numero/serie/data_emissao/valor_total sao read_only porque a nota e
        # a prova da despesa: os quatro vem do XML importado (xml_bruto), que
        # fica intacto no banco, e deixa-los gravaveis faria o relatorio e a
        # prova discordarem em silencio — o valor mudaria na tela sem o XML
        # mudar junto. Classificar e dizer a que despesa a nota pertence, nao
        # reescrever o que a nota diz.
        extra_kwargs = {
            "numero": {"read_only": True},
            "serie": {"read_only": True},
            "data_emissao": {"read_only": True},
            "valor_total": {"read_only": True},
        }
