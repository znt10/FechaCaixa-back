import importlib
from datetime import date
from decimal import Decimal

from django.apps import apps as django_apps
from django.test import TestCase, override_settings
from django.utils import timezone

from app.models import Assinatura, Conta
from app.services.mensalidade.datas import dia_de_vencimento_para, mes_seguinte


class DatasTests(TestCase):
    def test_mes_seguinte_vira_o_ano_em_dezembro(self):
        self.assertEqual(mes_seguinte(date(2026, 12, 15), 15), date(2027, 1, 15))

    def test_mes_seguinte_usa_o_dia_pedido(self):
        self.assertEqual(mes_seguinte(date(2026, 1, 28), 10), date(2026, 2, 10))

    def test_dia_de_vencimento_para_no_maximo_28(self):
        self.assertEqual(dia_de_vencimento_para(date(2026, 1, 31)), 28)
        self.assertEqual(dia_de_vencimento_para(date(2026, 1, 9)), 9)


class ContaNovaTests(TestCase):
    def test_nasce_com_o_primeiro_mes_por_conta_da_casa(self):
        hoje = timezone.localdate()
        conta = Conta.objects.create(nome="Padaria Nova")

        assinatura = Assinatura.objects.get(conta=conta)
        dia = min(hoje.day, 28)
        self.assertFalse(assinatura.isenta)
        self.assertFalse(assinatura.nunca_bloquear)
        self.assertEqual(assinatura.valor_mensal, Decimal("100.00"))
        self.assertEqual(assinatura.dia_de_vencimento, dia)
        self.assertEqual(assinatura.paga_ate, mes_seguinte(hoje, dia))

    @override_settings(MENSALIDADE_VALOR_PADRAO=Decimal("80.00"))
    def test_valor_padrao_vem_da_configuracao(self):
        conta = Conta.objects.create(nome="Padaria Oitenta")
        self.assertEqual(Assinatura.objects.get(conta=conta).valor_mensal, Decimal("80.00"))

    def test_salvar_de_novo_nao_cria_outra_assinatura(self):
        conta = Conta.objects.create(nome="Padaria Uma")
        conta.nome = "Padaria Uma So"
        conta.save()
        self.assertEqual(Assinatura.objects.filter(conta=conta).count(), 1)


class MigrationDasContasExistentesTests(TestCase):
    def test_conta_sem_assinatura_ganha_uma_isenta(self):
        conta = Conta.objects.create(nome="Antiga")
        Assinatura.objects.filter(conta=conta).delete()

        migration = importlib.import_module(
            "app.migrations.0056_assinaturas_das_contas_existentes"
        )
        migration.criar_assinaturas(django_apps, None)

        assinatura = Assinatura.objects.get(conta=conta)
        self.assertTrue(assinatura.isenta)
        self.assertEqual(assinatura.paga_ate, timezone.localdate())

    def test_nao_mexe_em_quem_ja_tem(self):
        conta = Conta.objects.create(nome="Nova")
        antes = Assinatura.objects.get(conta=conta)

        migration = importlib.import_module(
            "app.migrations.0056_assinaturas_das_contas_existentes"
        )
        migration.criar_assinaturas(django_apps, None)

        depois = Assinatura.objects.get(conta=conta)
        self.assertEqual(depois.pk, antes.pk)
        self.assertFalse(depois.isenta)
