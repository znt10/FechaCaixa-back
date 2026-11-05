from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from app.notifications.tokens import gerar_token_senha
from app.services.acessos import criar_gerente, mandar_link, situacao_do_gerente

from .fabricas import conta_padrao


class CriarGerenteTests(TestCase):
    def setUp(self):
        self.conta = conta_padrao()

    def test_com_senha_nasce_ativo_e_entra(self):
        user = criar_gerente(self.conta, "Ana", "ana@x.com", "segredo1")
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("segredo1"))
        self.assertEqual(list(user.groups.values_list("name", flat=True)), ["Gerente"])
        self.assertEqual(user.perfil.conta, self.conta)
        self.assertEqual((user.username, user.first_name), ("ana@x.com", "Ana"))
        self.assertEqual(situacao_do_gerente(user), "ATIVO")

    def test_sem_senha_nasce_aguardando(self):
        for indice, senha in enumerate((None, "", "   ")):
            user = criar_gerente(self.conta, "Ana", f"ana{indice}@x.com", senha)
            self.assertFalse(user.has_usable_password())
            self.assertFalse(user.is_active)
            self.assertEqual(situacao_do_gerente(user), "AGUARDANDO_SENHA")

    def test_desativado(self):
        user = criar_gerente(self.conta, "Ana", "ana@x.com", "segredo1")
        user.is_active = False
        self.assertEqual(situacao_do_gerente(user), "DESATIVADO")

    def test_email_normalizado(self):
        user = criar_gerente(self.conta, "Ana", " Ana@X.com ", "segredo1")
        self.assertEqual((user.username, user.email), ("ana@x.com", "ana@x.com"))

    def test_email_repetido(self):
        criar_gerente(self.conta, "Ana", "ana@x.com", "segredo1")
        with self.assertRaises(ValidationError) as erro:
            criar_gerente(self.conta, "Outra", "ANA@x.com", "segredo1")
        self.assertIn("Este email ja esta em uso.", str(erro.exception.detail))

    def test_senha_curta(self):
        with self.assertRaises(ValidationError) as erro:
            criar_gerente(self.conta, "Ana", "ana@x.com", "12345")
        self.assertIn("A senha deve conter pelo menos 6 caracteres.", str(erro.exception.detail))
        self.assertFalse(User.objects.filter(username="ana@x.com").exists())


class MandarLinkTests(TestCase):
    def setUp(self):
        self.user = criar_gerente(conta_padrao(), "Ana", "ana@x.com")

    def test_mandar_link_envia_o_email_de_definir_senha(self):
        self.assertTrue(mandar_link(self.user))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ana@x.com"])
        self.assertIn("/redefinir-senha/", mail.outbox[0].body)

    def test_mandar_link_com_smtp_falhando_devolve_false(self):
        with mock.patch(
            "app.services.acessos.enviar_email_definir_senha", side_effect=OSError("smtp")
        ):
            self.assertFalse(mandar_link(self.user))

    def test_link_ativa_e_deixa_entrar(self):
        cliente = APIClient()
        self.assertFalse(cliente.login(username="ana@x.com", password="NovaSenha!23"))

        resposta = cliente.post(
            f"/api/v1/user/definir-senha/{gerar_token_senha(self.user)}/",
            {"password": "NovaSenha!23"},
            format="json",
        )

        self.assertEqual(resposta.status_code, 200, resposta.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertTrue(cliente.login(username="ana@x.com", password="NovaSenha!23"))
