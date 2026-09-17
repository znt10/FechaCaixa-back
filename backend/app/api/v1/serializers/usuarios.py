from django.contrib.auth.models import Group, User
from app.grupos import GRUPO_GERENTE
from rest_framework import serializers


class UsuarioSerializer(serializers.ModelSerializer):
    # Sobrou um valor so, e o campo continua obrigatorio de proposito: quem
    # chama /user/registrar/ tem que dizer o que esta criando. Ja houve
    # "responsavel" aqui, o login proprio da loja — a loja nao faz mais login.
    tipo_usuario = serializers.ChoiceField(choices=["gerente"], write_only=True)

    class Meta:
        model = User
        fields = ["id", "first_name", "email", "password", "tipo_usuario"]
        extra_kwargs = {"password": {"write_only": True}}

    def validate_password(self, value):
        if len(value) < 6:
            raise serializers.ValidationError(
                "A senha deve conter pelo menos 6 caracteres."
            )
        return value

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Este email ja esta em uso.")
        return value

    def create(self, validated_data):
        senha = validated_data.pop("password")
        email = validated_data.get("email")
        validated_data.pop("tipo_usuario")

        user = User(**validated_data)
        user.set_password(senha)
        user.username = email
        user.email = email
        user.save()

        grupo, _ = Group.objects.get_or_create(name=GRUPO_GERENTE)
        user.groups.add(grupo)

        return user
