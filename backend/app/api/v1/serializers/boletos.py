from rest_framework import serializers

from app.models import Boleto, Loja

from .notas import ElementoPorIdPublico

# O que a gerencia corrige na mao. O resto veio da loja ou da leitura.
CAMPOS_EDITAVEIS = ["vencimento", "valor", "beneficiario_nome", "descricao", "loja"]


class BoletoSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    # Queryset global: so responde "existe". Quem responde "e seu" e a view.
    loja = ElementoPorIdPublico(
        slug_field="public_id", queryset=Loja.objects.all(), allow_null=True, required=False
    )
    loja_nome = serializers.CharField(source="loja.nome_loja", read_only=True, default=None)
    pago_por_nome = serializers.SerializerMethodField()
    tem_arquivo = serializers.SerializerMethodField()

    class Meta:
        model = Boleto
        fields = [
            "id",
            "loja",
            "loja_nome",
            "origem",
            "situacao",
            "conferir",
            "vencimento",
            "valor",
            "beneficiario_nome",
            "beneficiario_documento",
            "linha_digitavel",
            "descricao",
            "arquivo_tipo",
            "arquivo_nome",
            "tem_arquivo",
            "created_at",
            "pago_em",
            "pago_por_nome",
        ]
        read_only_fields = [campo for campo in fields if campo not in CAMPOS_EDITAVEIS]

    def get_tem_arquivo(self, boleto):
        # Pelo tipo, e nao pelos bytes: a lista carrega o boleto com o arquivo
        # adiado, e ler `arquivo` aqui buscaria a foto de cada linha. O registro
        # grava os dois juntos.
        return bool(boleto.arquivo_tipo)

    def get_pago_por_nome(self, boleto):
        quem = boleto.pago_por
        if quem is None:
            return None
        return quem.first_name or quem.email or quem.username
