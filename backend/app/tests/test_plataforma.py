from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from app.models import Assinatura, Conta, Pagamento

from .fabricas import vincular_conta

LISTA = "/api/v1/plataforma/empresas/"


@override_settings(MENSALIDADE_EMAIL_AVISO="dono@fechacaixa.io")
class PlataformaTests(TestCase):
    def setUp(self):
        self.dono = User.objects.create_superuser(username="dono", password="x")
        self.api = APIClient()
        self.api.force_authenticate(self.dono)
        self.hoje = timezone.localdate()

    def empresa(self, nome, atraso=-20, **mudanca):
        conta = Conta.objects.create(nome=nome)
        paga_ate = self.hoje - timedelta(days=atraso)
        # O dia de vencimento acompanha o paga ate, como o sistema grava: sem
        # isso, perto da virada do ano o "mes seguinte" do teste cairia antes
        # de hoje.
        Assinatura.objects.filter(conta=conta).update(
            paga_ate=paga_ate, dia_de_vencimento=min(paga_ate.day, 28), **mudanca
        )
        return conta

    def aviso(self, conta):
        return Pagamento.objects.create(
            assinatura=Assinatura.objects.get(conta=conta),
            status="AGUARDANDO", origem="JA_PAGUEI", valor="100.00",
        )

    def gerente_de(self, conta):
        user = User.objects.create_user(
            username=f"ger@{conta.slug}.com", email=f"ger@{conta.slug}.com", password="x"
        )
        user.groups.add(Group.objects.get_or_create(name="Gerente")[0])
        vincular_conta(user, conta)
        return user


class AcessoTests(PlataformaTests):
    def test_so_o_superuser(self):
        conta = self.empresa("Padaria")
        for grupo in ("Gerente", "Admin"):
            user = User.objects.create_user(username=f"{grupo}@x.com", password="x")
            user.groups.add(Group.objects.get_or_create(name=grupo)[0])
            vincular_conta(user, conta)
            api = APIClient()
            api.force_authenticate(user)
            with self.subTest(grupo=grupo):
                self.assertEqual(api.get(LISTA).status_code, 403)


class ListaTests(PlataformaTests):
    def test_ordem_e_contadores(self):
        self.empresa("A em dia")
        self.empresa("B bloqueada", atraso=10)
        self.aviso(self.empresa("C aguardando", atraso=10))
        self.empresa("D atrasada", atraso=2)

        resp = self.api.get(LISTA)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            [e["nome"] for e in resp.data["empresas"]],
            ["C aguardando", "B bloqueada", "D atrasada", "A em dia"],
        )
        self.assertEqual(
            resp.data["contadores"], {"aguardando": 1, "atrasadas": 1, "bloqueadas": 1}
        )
        aguardando = resp.data["empresas"][0]["aguardando"]
        self.assertEqual(aguardando["valor"], "100.00")


class MudancaTests(PlataformaTests):
    def url(self, conta):
        return f"{LISTA}{conta.public_id}/"

    def test_muda_valor_isenta_e_nunca_bloquear(self):
        conta = self.empresa("Padaria")
        resp = self.api.patch(
            self.url(conta),
            {"valor_mensal": "120.00", "isenta": True, "nunca_bloquear": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        assinatura = Assinatura.objects.get(conta=conta)
        self.assertEqual(str(assinatura.valor_mensal), "120.00")
        self.assertTrue(assinatura.isenta and assinatura.nunca_bloquear)
        self.assertEqual(resp.data["situacao"], "ISENTA")

    def test_desativar_e_ativar(self):
        conta = self.empresa("Padaria")
        resp = self.api.patch(self.url(conta), {"ativo": False}, format="json")
        self.assertEqual(resp.data["situacao"], "DESATIVADA")
        conta.refresh_from_db()
        self.assertFalse(conta.ativo)

        self.api.patch(self.url(conta), {"ativo": True}, format="json")
        conta.refresh_from_db()
        self.assertTrue(conta.ativo)

    def test_paga_ate_acerta_o_dia(self):
        conta = self.empresa("Padaria")
        self.api.patch(self.url(conta), {"paga_ate": "2026-12-05"}, format="json")
        assinatura = Assinatura.objects.get(conta=conta)
        self.assertEqual((assinatura.paga_ate, assinatura.dia_de_vencimento), (date(2026, 12, 5), 5))

    def test_valor_zero_da_400(self):
        conta = self.empresa("Padaria")
        resp = self.api.patch(self.url(conta), {"valor_mensal": "0"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_empresa_inexistente_da_404(self):
        resp = self.api.patch(
            f"{LISTA}00000000-0000-0000-0000-000000000000/", {"isenta": True}, format="json"
        )
        self.assertEqual(resp.status_code, 404)


class PagamentosTests(PlataformaTests):
    def test_registrar_pagamento(self):
        conta = self.empresa("Padaria", atraso=10)
        resp = self.api.post(
            f"{LISTA}{conta.public_id}/registrar-pagamento/",
            {"paga_ate": "2026-12-05", "valor": "100.00", "observacao": "dinheiro"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        pagamento = Pagamento.objects.get()
        self.assertEqual((pagamento.origem, pagamento.status), ("MANUAL", "CONFIRMADO"))
        self.assertEqual(resp.data["paga_ate"], "2026-12-05")

    def test_confirmar_e_confirmar_de_novo(self):
        conta = self.empresa("Padaria", atraso=2)
        self.gerente_de(conta)
        pagamento = self.aviso(conta)
        url = f"/api/v1/plataforma/pagamentos/{pagamento.public_id}/confirmar/"

        primeira = self.api.post(url)
        segunda = self.api.post(url)

        self.assertEqual(primeira.status_code, 200, primeira.data)
        self.assertEqual(segunda.status_code, 400)
        self.assertEqual(len(mail.outbox), 1)
        assinatura = Assinatura.objects.get(conta=conta)
        self.assertGreater(assinatura.paga_ate, self.hoje)

    def test_recusar(self):
        conta = self.empresa("Padaria", atraso=10)
        self.gerente_de(conta)
        pagamento = self.aviso(conta)

        resp = self.api.post(f"/api/v1/plataforma/pagamentos/{pagamento.public_id}/recusar/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["situacao"], "BLOQUEADA")
        pagamento.refresh_from_db()
        self.assertEqual(pagamento.status, "RECUSADO")
