"""Os comprovantes no /admin: achar e apagar os de teste.

Apagar pelo admin remove de verdade, e isso e o que se quer: o comprovante de
teste segura o ID da transacao, e o mesmo Pix mandado depois "a valer" voltaria
como ja lancado.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from app.models import Comprovante, RemetenteDeComprovante

from .fabricas import conta_padrao


class ComprovantesNoAdminTests(TestCase):
    def setUp(self):
        self.conta = conta_padrao()
        self.comprovante = Comprovante.objects.create(
            conta=self.conta,
            origem=Comprovante.Origem.EMAIL,
            situacao=Comprovante.Situacao.OK,
            valor=Decimal("4.00"),
            recebedor_nome="Recebedor de Teste",
            id_transacao="E00000000202610130000aaaa0042",
            arquivo=b"%PDF-1.4 inventado",
            arquivo_tipo="application/pdf",
        )
        dono = User.objects.create_superuser(
            username="dono@fechacaixa.local", email="dono@fechacaixa.local",
            password="uma-senha-de-teste",
        )
        self.client.force_login(dono)

    def test_a_lista_mostra_o_comprovante_com_a_empresa(self):
        resposta = self.client.get("/admin/app/comprovante/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Recebedor de Teste")
        self.assertContains(resposta, "field-conta")

    def test_apagar_pelo_admin_libera_o_id_da_transacao(self):
        resposta = self.client.post(
            f"/admin/app/comprovante/{self.comprovante.pk}/delete/", {"post": "yes"}
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertFalse(Comprovante.objects.filter(id_transacao="E00000000202610130000aaaa0042").exists())

    def test_remetente_se_cadastra_pelo_admin(self):
        resposta = self.client.post(
            "/admin/app/remetentedecomprovante/add/",
            {"conta": self.conta.pk, "email": "Alguem@Gmail.com"},
        )
        self.assertEqual(resposta.status_code, 302, getattr(resposta, "context", None) and resposta.context.get("errors"))
        self.assertEqual(RemetenteDeComprovante.objects.get().email, "alguem@gmail.com")

    def test_a_pagina_do_comprovante_abre(self):
        resposta = self.client.get(f"/admin/app/comprovante/{self.comprovante.pk}/change/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "E00000000202610130000aaaa0042")
