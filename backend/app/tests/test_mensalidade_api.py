from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from app.models import Assinatura, Conta, Pagamento

from .fabricas import vincular_conta

URL = "/api/v1/minha-mensalidade/"
JA_PAGUEI = "/api/v1/minha-mensalidade/ja-paguei/"


@override_settings(MENSALIDADE_EMAIL_AVISO="dono@fechacaixa.io")
class MinhaMensalidadeTests(TestCase):
    def setUp(self):
        self.conta = Conta.objects.create(nome="Padaria Central")
        self.gerente = self.usuario("ger@x.com", "Gerente")
        self.api = self.entrar(self.gerente)

    def usuario(self, email, grupo):
        user = User.objects.create_user(username=email, email=email, password="x")
        user.groups.add(Group.objects.get_or_create(name=grupo)[0])
        vincular_conta(user, self.conta)
        return user

    def entrar(self, user):
        api = APIClient()
        api.cookies["access_token"] = str(AccessToken.for_user(user))
        return api

    def atrasar(self, dias):
        Assinatura.objects.filter(conta=self.conta).update(
            paga_ate=timezone.localdate() - timedelta(days=dias)
        )

    def test_conta_nova_esta_em_dia(self):
        resp = self.api.get(URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["situacao"], "EM_DIA")
        self.assertEqual(resp.data["valor"], "100.00")
        self.assertTrue(resp.data["pode_informar"])

    def test_sem_chave_pix_nao_ha_pix(self):
        self.assertIsNone(self.api.get(URL).data["pix"])

    @override_settings(PIX_CHAVE="+5511999998888", PIX_NOME_RECEBEDOR="Maria da Silva", PIX_CIDADE="Patos")
    def test_com_chave_pix_vem_o_copia_e_cola_e_o_qr(self):
        pix = self.api.get(URL).data["pix"]
        self.assertIn("+5511999998888", pix["copia_e_cola"])
        self.assertIn("5406100.00", pix["copia_e_cola"])
        self.assertTrue(pix["qr_svg"].startswith("data:image/svg+xml"))

    def test_bloqueada_ainda_ve_a_mensalidade_e_avisa(self):
        self.atrasar(10)
        self.assertEqual(self.api.get(URL).data["situacao"], "BLOQUEADA")

        resp = self.api.post(JA_PAGUEI)

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["situacao"], "AGUARDANDO")
        self.assertFalse(resp.data["pode_informar"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.api.get("/api/v1/fechamentos-caixa/").status_code, 200)

    def test_segundo_aviso_da_400(self):
        self.api.post(JA_PAGUEI)
        resp = self.api.post(JA_PAGUEI)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("aguardando confirmação", resp.data["error"])
        self.assertEqual(Pagamento.objects.count(), 1)

    def test_funcionario_nao_avisa(self):
        api = self.entrar(self.usuario("func@x.com", "Funcionario"))
        self.assertFalse(api.get(URL).data["pode_informar"])
        resp = api.post(JA_PAGUEI)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("gerente", resp.data["error"])

    def test_desativada_nao_avisa(self):
        self.conta.ativo = False
        self.conta.save(update_fields=["ativo"])
        self.assertEqual(self.api.get(URL).data["situacao"], "DESATIVADA")
        self.assertEqual(self.api.post(JA_PAGUEI).status_code, 400)

    def test_superuser_e_isento(self):
        dono = User.objects.create_superuser(username="dono", password="x")
        self.assertEqual(self.entrar(dono).get(URL).data, {"situacao": "ISENTA"})

    def test_me_diz_quem_e_superuser(self):
        dono = User.objects.create_superuser(username="dono", password="x")
        self.assertIs(self.entrar(dono).get("/api/v1/user/me/").data["super"], True)
        self.assertIs(self.api.get("/api/v1/user/me/").data["super"], False)
