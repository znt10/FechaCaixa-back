"""Serializer da Conta — o negocio/cliente dono das lojas."""

from rest_framework import serializers

from app.models import Conta


class ContaSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)

    class Meta:
        model = Conta
        fields = ["id", "nome", "slug", "ativo"]
