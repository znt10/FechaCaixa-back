import hashlib
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from app.models import Assinatura, Conta, DispositivoDoFormulario

from .fabricas import criar_loja


class FormularioDaEmpresaTravadaTests(TestCase):
    def setUp(self):
        self.conta = Conta.objects.create(nome="Padaria Central")
        criar_loja(conta=self.conta, nome_loja="Loja 1", cidade="Patos", endereco="Rua 1")
        token = "token-do-aparelho-da-loja-123456"
        DispositivoDoFormulario.objects.create(
            conta=self.conta, apelido="Tablet",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        self.api = APIClient()
        self.api.cookies["formulario_token"] = token

    def test_desativada_diz_que_esta_desativada(self):
        self.conta.ativo = False
        self.conta.save(update_fields=["ativo"])

        resp = self.api.get("/api/v1/formulario/empresa/", HTTP_X_FORMULARIO="1")

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data["code"], "empresa_desativada")

    def test_codigo_novo_de_empresa_desativada_continua_invalido(self):
        """Distinguir entregaria a existencia da empresa a quem chuta codigo."""
        self.conta.ativo = False
        self.conta.save(update_fields=["ativo"])

        resp = APIClient().post(
            "/api/v1/formulario/acesso/",
            {"codigo": self.conta.codigo_acesso, "apelido": "Celular"},
            format="json",
        )

        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.data["detail"], "Codigo invalido.")

    def test_bloqueada_por_atraso_segue_lancando(self):
        Assinatura.objects.filter(conta=self.conta).update(
            paga_ate=timezone.localdate() - timedelta(days=30)
        )
        self.assertEqual(
            self.api.get("/api/v1/formulario/empresa/", HTTP_X_FORMULARIO="1").status_code, 200
        )
        self.assertEqual(
            self.api.get("/api/v1/lojas/", HTTP_X_FORMULARIO="1").status_code, 200
        )
