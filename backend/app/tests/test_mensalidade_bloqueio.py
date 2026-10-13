import hashlib
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from app.models import Assinatura, Conta, DispositivoDoFormulario, Pagamento

from .fabricas import criar_loja, vincular_conta

PAINEL = "/api/v1/fechamentos-caixa/"


class BloqueioDoPainelTests(TestCase):
    def setUp(self):
        self.conta = Conta.objects.create(nome="Padaria Central")
        criar_loja(conta=self.conta, nome_loja="Loja 1", cidade="Patos", endereco="Rua 1")
        self.gerente = User.objects.create_user(username="ger@x.com", password="x")
        self.gerente.groups.add(Group.objects.get_or_create(name="Gerente")[0])
        vincular_conta(self.gerente, self.conta)
        self.api = APIClient()
        # Cookie de verdade, e nao force_authenticate: e na autenticacao que
        # a trava mora, e force_authenticate a pularia.
        self.api.cookies["access_token"] = str(AccessToken.for_user(self.gerente))

    def atrasar(self, dias, **mudanca):
        Assinatura.objects.filter(conta=self.conta).update(
            paga_ate=timezone.localdate() - timedelta(days=dias), **mudanca
        )

    def test_em_dia_passa(self):
        self.assertEqual(self.api.get(PAINEL).status_code, 200)

    def test_na_carencia_passa(self):
        self.atrasar(5)
        self.assertEqual(self.api.get(PAINEL).status_code, 200)

    def test_bloqueada_recebe_402_com_codigo(self):
        self.atrasar(6)
        resp = self.api.get(PAINEL)
        self.assertEqual(resp.status_code, 402)
        self.assertEqual(resp.data["code"], "mensalidade_bloqueada")
        self.assertIn("atrasada", resp.data["detail"])

    def test_bloqueada_ainda_ve_quem_esta_logado(self):
        self.atrasar(6)
        self.assertEqual(self.api.get("/api/v1/user/me/").status_code, 200)

    def test_nunca_bloquear_passa(self):
        self.atrasar(60, nunca_bloquear=True)
        self.assertEqual(self.api.get(PAINEL).status_code, 200)

    def test_aviso_pendente_segura(self):
        self.atrasar(20)
        Pagamento.objects.create(
            assinatura=Assinatura.objects.get(conta=self.conta),
            status="AGUARDANDO", origem="JA_PAGUEI", valor="100.00",
        )
        self.assertEqual(self.api.get(PAINEL).status_code, 200)

    def test_desativada_recebe_403_com_codigo(self):
        self.conta.ativo = False
        self.conta.save(update_fields=["ativo"])
        resp = self.api.get(PAINEL)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data["code"], "empresa_desativada")

    def test_sessao_tambem_trava(self):
        self.atrasar(6)
        api = APIClient()
        api.force_login(self.gerente)
        self.assertEqual(api.get(PAINEL).status_code, 402)

    def test_superuser_nunca_trava(self):
        dono = User.objects.create_superuser(username="dono", password="x")
        api = APIClient()
        api.cookies["access_token"] = str(AccessToken.for_user(dono))
        self.atrasar(90)
        self.assertEqual(api.get("/api/v1/lojas/").status_code, 200)

    def test_formulario_no_mesmo_aparelho_do_gerente_segue(self):
        """O gerente logado no tablet da loja: o formulario continua."""
        self.atrasar(6)
        token = "token-do-aparelho-da-loja-123456"
        DispositivoDoFormulario.objects.create(
            conta=self.conta, apelido="Tablet",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        self.api.cookies["formulario_token"] = token

        com_cabecalho = self.api.get(
            "/api/v1/formulario/empresa/", HTTP_X_FORMULARIO="1"
        )
        sem_cabecalho = self.api.get("/api/v1/formulario/empresa/")

        self.assertEqual(com_cabecalho.status_code, 200)
        self.assertEqual(sem_cabecalho.status_code, 402)
