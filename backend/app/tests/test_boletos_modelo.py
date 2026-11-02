"""O boleto e a chave da empresa que liga a funcionalidade.

A chave nasce desligada: nem toda empresa recebe boleto na loja, e quem nao
pediu nao pode amanhecer com uma pergunta a mais no formulario.
"""

from django.contrib.auth.models import Group, User
from django.test import TestCase
from rest_framework.test import APITestCase

from app.models import Boleto, Conta

from .fabricas import vincular_conta


class BoletoModeloTests(TestCase):
    def test_conta_nova_nasce_sem_boletos(self):
        self.assertIs(Conta.objects.create(nome="X").boletos_ativos, False)

    def test_boleto_nasce_a_pagar_sem_conferir(self):
        boleto = Boleto.objects.create(
            conta=Conta.objects.create(nome="X"), origem=Boleto.Origem.FORMULARIO
        )

        self.assertEqual(boleto.situacao, Boleto.Situacao.A_PAGAR)
        self.assertIs(boleto.conferir, False)
        self.assertEqual(boleto.campos_editados, [])


class ChaveDosBoletosTests(APITestCase):
    def setUp(self):
        self.conta = Conta.objects.create(nome="Aurora")

    def gerente(self):
        gerente = User.objects.create_user(username="g@aurora.com", password="x")
        gerente.groups.add(Group.objects.get_or_create(name="Gerente")[0])
        vincular_conta(gerente, self.conta)
        self.client.force_authenticate(user=gerente)

    def test_formulario_recebe_a_chave(self):
        self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": self.conta.codigo_acesso, "apelido": "Celular"},
            format="json",
        )

        resp = self.client.get("/api/v1/formulario/empresa/")

        self.assertEqual(resp.status_code, 200)
        self.assertIs(resp.data["boletos_ativos"], False)

    def test_empresa_liga_a_chave(self):
        self.gerente()

        resp = self.client.patch(
            "/api/v1/minha-empresa/", {"boletos_ativos": True}, format="json"
        )

        self.assertEqual(resp.status_code, 200)
        self.conta.refresh_from_db()
        self.assertIs(self.conta.boletos_ativos, True)

    def test_user_me_diz_se_a_empresa_usa_boletos(self):
        self.gerente()
        self.assertIs(self.client.get("/api/v1/user/me/").data["modulos"]["boletos"], False)

        self.conta.boletos_ativos = True
        self.conta.save()

        self.assertIs(self.client.get("/api/v1/user/me/").data["modulos"]["boletos"], True)
