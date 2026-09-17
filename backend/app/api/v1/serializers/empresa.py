"""A empresa administrando a si mesma — a tela de admin do FechaCaixa."""

from django.contrib.auth.models import Group, User
from rest_framework import serializers

from app.grupos import GRUPO_FUNCIONARIO
from app.models import Conta, DispositivoDoFormulario, PerfilUsuario


class MinhaEmpresaSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)

    class Meta:
        model = Conta
        fields = ["id", "nome", "slug", "codigo_acesso", "fechamentos_por_dia"]
        # Trocar o codigo derruba todos os aparelhos: e uma acao com
        # consequencia, feita por um botao que avisa, e nao a edicao de um campo
        # de texto que alguem salva sem perceber o que aconteceu.
        read_only_fields = ["nome", "slug", "codigo_acesso"]


class AparelhoSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)

    class Meta:
        model = DispositivoDoFormulario
        fields = ["id", "apelido", "criado_em", "ultimo_uso_em"]
        read_only_fields = ["criado_em", "ultimo_uso_em"]

    criado_em = serializers.DateTimeField(source="created_at", read_only=True)


class FuncionarioSerializer(serializers.ModelSerializer):
    """O login de conferencia, como a tela da empresa o mostra.

    `ativo` espelha is_active com outro nome de proposito: na tela a gerente
    desativa uma pessoa, nao um registro do Django.
    """

    nome = serializers.CharField(source="first_name", read_only=True)
    ativo = serializers.BooleanField(source="is_active", read_only=True)

    class Meta:
        model = User
        fields = ["id", "nome", "email", "ativo"]


class NovoFuncionarioSerializer(serializers.ModelSerializer):
    """Criacao. A empresa NAO esta aqui: ela vem do login de quem cria.

    Aceitar "de qual empresa" neste corpo transformaria "criar meu colega" em
    "criar um login dentro da empresa do vizinho".
    """

    class Meta:
        model = User
        fields = ["first_name", "email", "password"]
        extra_kwargs = {
            "password": {"write_only": True},
            "first_name": {"required": True, "allow_blank": False},
            "email": {"required": True, "allow_blank": False},
        }

    def validate_password(self, valor):
        if len(valor) < 6:
            raise serializers.ValidationError(
                "A senha deve conter pelo menos 6 caracteres."
            )
        return valor

    def validate_email(self, valor):
        valor = valor.lower().strip()
        # username=email neste projeto: sem esta checagem o create estouraria
        # com IntegrityError (500) em vez de dizer o que houve.
        if User.objects.filter(username=valor).exists() or User.objects.filter(
            email__iexact=valor
        ).exists():
            raise serializers.ValidationError("Este email ja esta em uso.")
        return valor

    def create(self, dados):
        conta = self.context["conta"]
        senha = dados.pop("password")
        email = dados["email"]

        user = User(**dados)
        user.username = email
        user.set_password(senha)
        user.save()
        user.groups.add(Group.objects.get_or_create(name=GRUPO_FUNCIONARIO)[0])
        PerfilUsuario.objects.create(user=user, conta=conta)
        return user
