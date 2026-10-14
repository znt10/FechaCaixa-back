from rest_framework import serializers

from app.models import Comprovante, ElementoDeDespesa, Loja, RemetenteDeComprovante

from .notas import ElementoPorIdPublico

# O que a pessoa corrige na mao. O resto veio do comprovante e e a prova do
# gasto: so leitura.
CAMPOS_EDITAVEIS = ["valor", "data_hora", "recebedor_nome", "loja", "elemento"]


class ComprovanteSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    # Querysets globais: so respondem "existe". Quem responde "e seu" e a view.
    loja = ElementoPorIdPublico(
        slug_field="public_id", queryset=Loja.objects.all(), allow_null=True, required=False
    )
    elemento = ElementoPorIdPublico(
        slug_field="public_id",
        queryset=ElementoDeDespesa.objects.all(),
        allow_null=True,
        required=False,
    )
    loja_nome = serializers.CharField(source="loja.nome_loja", read_only=True, default=None)
    elemento_nome = serializers.CharField(source="elemento.nome", read_only=True, default=None)
    tem_arquivo = serializers.SerializerMethodField()

    class Meta:
        model = Comprovante
        fields = [
            "id",
            "situacao",
            "motivo",
            "origem",
            "valor",
            "data_hora",
            "id_transacao",
            "pagador_nome",
            "pagador_documento",
            "pagador_banco",
            "recebedor_nome",
            "recebedor_documento",
            "recebedor_banco",
            "loja",
            "loja_nome",
            "elemento",
            "elemento_nome",
            "arquivo_nome",
            "arquivo_tipo",
            "tem_arquivo",
            "remetente",
            "created_at",
        ]
        read_only_fields = [
            campo for campo in fields if campo not in CAMPOS_EDITAVEIS
        ]

    def get_tem_arquivo(self, comprovante):
        return comprovante.arquivo is not None


class RemetenteDeComprovanteSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    # Sem o UniqueValidator automatico: a mensagem dele diria que o e-mail
    # existe, e a view responde sem contar de qual empresa ele e.
    email = serializers.EmailField()

    class Meta:
        model = RemetenteDeComprovante
        fields = ["id", "email"]
