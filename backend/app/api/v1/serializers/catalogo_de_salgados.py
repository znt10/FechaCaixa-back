"""Serializers do catalogo de salgados.

Sao lidos por duas pontas — o painel logado, que administra, e o formulario da
loja, que so escolhe — e por isso nao carregam nada alem do que o seletor
precisa.
"""

from rest_framework import serializers

from app.models import CategoriaDeSalgado, Salgado


class CategoriaDeSalgadoSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)

    class Meta:
        model = CategoriaDeSalgado
        fields = ["id", "nome", "ordem", "ativo"]


class SalgadoSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    categoria = serializers.SlugRelatedField(
        slug_field="public_id", queryset=CategoriaDeSalgado.objects.all()
    )
    categoria_nome = serializers.ReadOnlyField(source="categoria.nome")
    # O formulario da loja precisa disto para nao oferecer um item cuja
    # categoria foi desativada: o ViewSet devolve o catalogo inteiro de
    # proposito (a correcao precisa achar item inativo para reidratar), entao
    # quem filtra o que aparece no seletor e o front, e ele so sabe da
    # categoria com este campo.
    categoria_ativo = serializers.ReadOnlyField(source="categoria.ativo")

    class Meta:
        model = Salgado
        fields = ["id", "nome", "ativo", "categoria", "categoria_nome", "categoria_ativo"]
