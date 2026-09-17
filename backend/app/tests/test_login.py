"""A porta do sistema: POST /login/.

Este arquivo nasceu de um 500 em producao. A migracao 0037 tirou
Loja.responsavel (o login proprio da loja), e a LoginView continuou
perguntando por ele — `Loja.objects.filter(responsavel=user)`. Ninguem
entrava, e a suite inteira passava: 194 testes, e nenhum deles chegava a
bater na rota pela qual todo mundo entra.

O erro so aparece em runtime porque um campo inexistente e um FieldError na
hora da consulta, e nao um erro de importacao — nada acusa antes de alguem
tentar fazer login.
"""

from django.contrib.auth.models import Group, User
from rest_framework.test import APITestCase

from app.models import Conta, PerfilUsuario


class LoginTests(APITestCase):
    SENHA = "senha-de-teste-123"

    def setUp(self):
        for nome in ("Admin", "Gerente", "Funcionario"):
            Group.objects.get_or_create(name=nome)

        self.conta = Conta.objects.create(nome="Aurora Salgados")
        self.gerente = User.objects.create_user(
            username="ger@x.com",
            email="ger@x.com",
            password=self.SENHA,
            first_name="Zeca",
        )
        self.gerente.groups.add(Group.objects.get(name="Gerente"))
        PerfilUsuario.objects.create(user=self.gerente, conta=self.conta)

    def _entrar(self, email, senha):
        return self.client.post(
            "/login/", {"email": email, "password": senha}, format="json"
        )

    def test_o_gerente_entra(self):
        resposta = self._entrar("ger@x.com", self.SENHA)

        self.assertEqual(resposta.status_code, 200, getattr(resposta, "data", None))
        self.assertEqual(resposta.data["user"]["group"], "Gerente")
        self.assertEqual(resposta.data["user"]["first_name"], "Zeca")

    def test_os_tokens_vao_no_cookie_e_nao_no_corpo(self):
        """O JavaScript nunca ve o token: e o que segura um XSS."""
        resposta = self._entrar("ger@x.com", self.SENHA)

        self.assertIn("access_token", resposta.cookies)
        self.assertIn("refresh_token", resposta.cookies)
        self.assertTrue(resposta.cookies["access_token"]["httponly"])
        self.assertNotIn("access", resposta.data)
        self.assertNotIn("token", resposta.data)

    def test_senha_errada_nao_entra(self):
        resposta = self._entrar("ger@x.com", "outra-senha")

        self.assertEqual(resposta.status_code, 401)
        self.assertNotIn("access_token", resposta.cookies)

    def test_quem_nao_tem_cargo_nao_entra(self):
        """Sem grupo o sistema nao sabe o que mostrar — 403, e nao um painel
        vazio que parece conta quebrada."""
        sem_grupo = User.objects.create_user(
            username="ninguem@x.com", email="ninguem@x.com", password=self.SENHA
        )
        PerfilUsuario.objects.create(user=sem_grupo, conta=self.conta)

        resposta = self._entrar("ninguem@x.com", self.SENHA)

        self.assertEqual(resposta.status_code, 403)

    def test_conta_nao_confirmada_diz_o_que_falta(self):
        """Senha certa e conta inativa nao e "credencial invalida": a pessoa
        ficaria repetindo a senha certa sem entender."""
        pendente = User.objects.create_user(
            username="novo@x.com", email="novo@x.com", password=self.SENHA
        )
        pendente.is_active = False
        pendente.save(update_fields=["is_active"])

        resposta = self._entrar("novo@x.com", self.SENHA)

        self.assertEqual(resposta.status_code, 403)
        self.assertIn("confirmada", resposta.data["error"])
