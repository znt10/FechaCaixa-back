"""Plataforma → Acessos: empresas, modulos contratados e gerentes.

So do dono da plataforma (`DaPlataforma`). E o que antes so se fazia pelo
admin do Django: criar a empresa, ligar o que ela contratou e dar acesso ao
gerente, ja vinculado a ela.
"""

from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from app.grupos import GRUPO_GERENTE
from app.models import Conta
from app.services.acessos import (
    AGUARDANDO_SENHA,
    criar_gerente,
    mandar_link,
    situacao_do_gerente,
)

from .plataforma import DaPlataforma

# O nome na tela -> o campo da empresa. O que a empresa contrata, mais a tela
# CPF; o resto (catalogo, boletos...) o proprio gerente liga na tela Empresa.
MODULOS = {
    "notas": "modulo_notas_ativo",
    "banco": "modulo_banco_ativo",
    "comprovantes": "modulo_comprovantes_ativo",
    # A tela "Comprovantes CPF". O gerente tambem liga na tela Empresa; aqui
    # e para o dono ja deixar a empresa nova pronta.
    "comprovantes_pessoais": "comprovantes_pessoais_ativos",
}


class ModulosSerializer(serializers.Serializer):
    notas = serializers.BooleanField(required=False)
    banco = serializers.BooleanField(required=False)
    comprovantes = serializers.BooleanField(required=False)
    comprovantes_pessoais = serializers.BooleanField(required=False)


class NovoGerenteSerializer(serializers.Serializer):
    nome = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    senha = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)


class NovaEmpresaSerializer(serializers.Serializer):
    nome = serializers.CharField(max_length=150)
    modulos = ModulosSerializer(required=False)
    gerente = NovoGerenteSerializer()


def _gerentes(conta):
    return (
        User.objects.filter(perfil__conta=conta, groups__name=GRUPO_GERENTE)
        .order_by("first_name", "email")
        .distinct()
    )


def _gerente_na_tela(user):
    return {
        "id": user.id,
        "nome": user.first_name,
        "email": user.email,
        "situacao": situacao_do_gerente(user),
    }


def _empresa_na_tela(conta):
    return {
        "id": str(conta.public_id),
        "nome": conta.nome,
        "ativo": conta.ativo,
        "modulos": {nome: getattr(conta, campo) for nome, campo in MODULOS.items()},
        "gerentes": [_gerente_na_tela(user) for user in _gerentes(conta)],
    }


def _aplicar_modulos(conta, modulos):
    for nome, valor in modulos.items():
        setattr(conta, MODULOS[nome], valor)


class AcessosView(DaPlataforma):
    def get(self, request):
        return Response([_empresa_na_tela(conta) for conta in Conta.objects.order_by("nome")])

    def post(self, request):
        entrada = NovaEmpresaSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        nome = dados["nome"].strip()
        if not nome:
            raise ValidationError({"nome": ["Informe o nome da empresa."]})

        # Empresa e primeiro gerente juntos: um e-mail repetido no gerente nao
        # pode deixar uma empresa sem ninguem que entre nela.
        with transaction.atomic():
            conta = Conta(nome=nome)
            _aplicar_modulos(conta, dados.get("modulos", {}))
            conta.save()
            gerente = dados["gerente"]
            user = criar_gerente(conta, gerente["nome"], gerente["email"], gerente.get("senha"))

        # Depois do commit: o link nao pode apontar para um usuario desfeito.
        enviado = None if user.has_usable_password() else mandar_link(user)
        return Response(
            {**_empresa_na_tela(conta), "email_enviado": enviado},
            status=status.HTTP_201_CREATED,
        )


class AcessosDaEmpresaView(DaPlataforma):
    def patch(self, request, public_id):
        conta = get_object_or_404(Conta, public_id=public_id)
        entrada = ModulosSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        _aplicar_modulos(conta, entrada.validated_data)
        conta.save(update_fields=[MODULOS[n] for n in entrada.validated_data] + ["updated_at"])
        return Response(_empresa_na_tela(conta))


class GerentesDaEmpresaView(DaPlataforma):
    def post(self, request, public_id):
        conta = get_object_or_404(Conta, public_id=public_id)
        entrada = NovoGerenteSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        with transaction.atomic():
            dados = entrada.validated_data
            user = criar_gerente(conta, dados["nome"], dados["email"], dados.get("senha"))
        enviado = None if user.has_usable_password() else mandar_link(user)
        return Response(
            {"gerente": _gerente_na_tela(user), "email_enviado": enviado},
            status=status.HTTP_201_CREATED,
        )


class ReenviarLinkView(DaPlataforma):
    def post(self, request, public_id, user_id):
        conta = get_object_or_404(Conta, public_id=public_id)
        # Gerente DESTA empresa: o id na URL nao alcanca o usuario de outra.
        user = get_object_or_404(_gerentes(conta), id=user_id)
        if situacao_do_gerente(user) != AGUARDANDO_SENHA:
            raise ValidationError({"error": "Este gerente ja tem senha."})
        return Response({"email_enviado": mandar_link(user)})
