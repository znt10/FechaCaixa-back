"""A tela Plataforma: todas as empresas, so para o dono da plataforma."""

from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from app.models import Assinatura, Conta, Pagamento
from app.permissions import IsSuperuser
from app.services.mensalidade import (
    RecusaDaMensalidade,
    assinatura_da,
    confirmar_pagamento,
    recusar_pagamento,
    registrar_pagamento_manual,
)
from app.services.mensalidade.datas import dia_de_vencimento_para
from app.services.mensalidade.plataforma import linha_da_empresa, painel_da_plataforma

CENTAVO = Decimal("0.01")


class MudancaDaEmpresaSerializer(serializers.Serializer):
    valor_mensal = serializers.DecimalField(
        max_digits=8, decimal_places=2, min_value=CENTAVO, required=False
    )
    isenta = serializers.BooleanField(required=False)
    nunca_bloquear = serializers.BooleanField(required=False)
    ativo = serializers.BooleanField(required=False)
    paga_ate = serializers.DateField(required=False)


class NovoPagamentoSerializer(serializers.Serializer):
    paga_ate = serializers.DateField()
    valor = serializers.DecimalField(max_digits=8, decimal_places=2, min_value=CENTAVO)
    observacao = serializers.CharField(max_length=500, required=False, allow_blank=True)


class DaPlataforma(APIView):
    permission_classes = [IsAuthenticated, IsSuperuser]


class EmpresasDaPlataformaView(DaPlataforma):
    def get(self, request):
        return Response(painel_da_plataforma())


class EmpresaDaPlataformaView(DaPlataforma):
    def patch(self, request, public_id):
        conta = get_object_or_404(Conta, public_id=public_id)
        entrada = MudancaDaEmpresaSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data

        if "ativo" in dados:
            conta.ativo = dados["ativo"]
            conta.save(update_fields=["ativo", "updated_at"])

        campos = [c for c in ("valor_mensal", "isenta", "nunca_bloquear", "paga_ate") if c in dados]
        if campos:
            assinatura = assinatura_da(conta) or Assinatura.primeiro_mes_de_graca(conta)
            for campo in campos:
                setattr(assinatura, campo, dados[campo])
            if "paga_ate" in dados:
                assinatura.dia_de_vencimento = dia_de_vencimento_para(dados["paga_ate"])
                campos.append("dia_de_vencimento")
            assinatura.save(update_fields=[*campos, "updated_at"])

        return Response(linha_da_empresa(Conta.objects.get(pk=conta.pk)))


class RegistrarPagamentoView(DaPlataforma):
    def post(self, request, public_id):
        conta = get_object_or_404(Conta, public_id=public_id)
        entrada = NovoPagamentoSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        assinatura = assinatura_da(conta) or Assinatura.primeiro_mes_de_graca(conta)
        registrar_pagamento_manual(assinatura, request.user, **entrada.validated_data)
        return Response(linha_da_empresa(Conta.objects.get(pk=conta.pk)), status=201)


class ConfirmarPagamentoView(DaPlataforma):
    operacao = staticmethod(confirmar_pagamento)

    def post(self, request, public_id):
        pagamento = get_object_or_404(
            Pagamento.objects.select_related("assinatura__conta"), public_id=public_id
        )
        try:
            self.operacao(pagamento, request.user)
        except RecusaDaMensalidade as recusa:
            return Response({"error": recusa.mensagem}, status=400)
        return Response(linha_da_empresa(Conta.objects.get(pk=pagamento.assinatura.conta_id)))


class RecusarPagamentoView(ConfirmarPagamentoView):
    operacao = staticmethod(recusar_pagamento)
