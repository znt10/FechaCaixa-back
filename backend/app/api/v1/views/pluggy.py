"""A Pluggy na tela Plataforma: so para o dono da plataforma."""

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from app.models import Conta, ConexaoPluggy, ContaBancaria
from app.services.pluggy.busca import ESTADOS_QUE_BUSCAM, buscar_conexao
from app.services.pluggy.cifra import cifra_configurada
from app.services.pluggy.painel import (
    ERRO_SEM_CHAVE,
    ErroDoTeste,
    linha_da_conexao,
    painel_da_pluggy,
    testar_conexao,
)

from .plataforma import DaPlataforma

ERRO_CONTA_DE_OUTRA_EMPRESA = "Esta conta bancária é de outra empresa."
ERRO_CONTA_JA_LIGADA = "Esta conta da Pluggy já está ligada a outra conta bancária."
ERRO_BUSCAR_SEM_ATIVAR = "Ative a conexão antes de buscar."


class NovaConexaoSerializer(serializers.Serializer):
    empresa_id = serializers.UUIDField()
    email = serializers.EmailField()


class MudancaDaConexaoSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False)
    client_id = serializers.CharField(max_length=64, required=False, allow_blank=True)
    client_secret = serializers.CharField(required=False, allow_blank=False)
    item_ids = serializers.CharField(required=False, allow_blank=True)
    estado = serializers.ChoiceField(
        choices=[ConexaoPluggy.Estado.DESLIGADA, ConexaoPluggy.Estado.ATIVA],
        required=False,
    )


class VinculoSerializer(serializers.Serializer):
    pluggy_account_id = serializers.CharField(max_length=64)
    conta_bancaria_id = serializers.UUIDField()


class VinculosSerializer(serializers.Serializer):
    vinculos = VinculoSerializer(many=True, allow_empty=False)


def _exigir_cifra():
    if not cifra_configurada():
        raise ValidationError({"detail": ERRO_SEM_CHAVE})


def _conexao(public_id):
    return get_object_or_404(
        ConexaoPluggy.objects.select_related("conta"), public_id=public_id
    )


class PluggyDaPlataformaView(DaPlataforma):
    def get(self, request):
        return Response(painel_da_pluggy())

    def post(self, request):
        entrada = NovaConexaoSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        _exigir_cifra()
        conta = get_object_or_404(Conta, public_id=entrada.validated_data["empresa_id"])
        with transaction.atomic():
            conexao = ConexaoPluggy.objects.create(
                conta=conta, email=entrada.validated_data["email"]
            )
            if not conta.modulo_banco_ativo:
                conta.modulo_banco_ativo = True
                conta.save(update_fields=["modulo_banco_ativo", "updated_at"])
        return Response(linha_da_conexao(conexao), status=201)


class ConexaoPluggyView(DaPlataforma):
    def patch(self, request, public_id):
        conexao = _conexao(public_id)
        entrada = MudancaDaConexaoSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = dict(entrada.validated_data)
        segredo = dados.pop("client_secret", None)
        # So o que veio no PATCH: salvar a linha inteira desfaria o que a
        # busca gravou (estado, erro, falhas) depois de a conexao ser lida.
        campos = list(dados) + ["updated_at"]
        if segredo is not None:
            _exigir_cifra()
            conexao.guardar_segredo(segredo)
            campos += ["client_secret_cifrado", "segredo_salvo_em"]
        for campo, valor in dados.items():
            setattr(conexao, campo, valor)
        conexao.save(update_fields=campos)
        conexao.refresh_from_db()
        return Response(linha_da_conexao(conexao))


class TestarConexaoPluggyView(DaPlataforma):
    def post(self, request, public_id):
        conexao = _conexao(public_id)
        try:
            return Response(testar_conexao(conexao))
        except ErroDoTeste as erro:
            raise ValidationError({"detail": erro.mensagem}) from None


class VincularContasPluggyView(DaPlataforma):
    def post(self, request, public_id):
        conexao = _conexao(public_id)
        entrada = VinculosSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)

        # Valida todos antes de gravar o primeiro.
        pares = []
        for vinculo in entrada.validated_data["vinculos"]:
            conta_bancaria = get_object_or_404(
                ContaBancaria.objects.select_related("loja"),
                public_id=vinculo["conta_bancaria_id"],
            )
            if conta_bancaria.loja.conta_id != conexao.conta_id:
                raise ValidationError({"detail": ERRO_CONTA_DE_OUTRA_EMPRESA})
            conta_pluggy = vinculo["pluggy_account_id"]
            if (
                ContaBancaria.objects.filter(pluggy_account_id=conta_pluggy)
                .exclude(pk=conta_bancaria.pk)
                .exists()
            ):
                raise ValidationError({"detail": ERRO_CONTA_JA_LIGADA})
            pares.append((conta_bancaria, conta_pluggy))

        with transaction.atomic():
            for conta_bancaria, conta_pluggy in pares:
                conta_bancaria.pluggy_account_id = conta_pluggy
                conta_bancaria.conexao_pluggy = conexao
                conta_bancaria.save(
                    update_fields=["pluggy_account_id", "conexao_pluggy", "updated_at"]
                )
            conexao.estado = ConexaoPluggy.Estado.ATIVA
            conexao.save(update_fields=["estado", "updated_at"])

        buscar_conexao(conexao)
        conexao.refresh_from_db()
        return Response(linha_da_conexao(conexao))


class BuscarConexaoPluggyView(DaPlataforma):
    def post(self, request, public_id):
        conexao = _conexao(public_id)
        # Em conexao configurando, a primeira busca boa mandaria o e-mail de
        # "conectada" de uma conexao que o dono ainda nao ligou.
        if conexao.estado not in ESTADOS_QUE_BUSCAM:
            raise ValidationError({"detail": ERRO_BUSCAR_SEM_ATIVAR})
        buscar_conexao(conexao)
        conexao.refresh_from_db()
        return Response(linha_da_conexao(conexao))
