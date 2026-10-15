"""A renovacao da sessao regrava o papel que o front usa para abrir as telas."""

from django.contrib.auth.models import Group, User
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


class RenovarSessaoRegravaPapelTests(TestCase):
    def renovar(self, user, papel_antigo="Admin"):
        cliente = APIClient()
        cliente.cookies["refresh_token"] = str(RefreshToken.for_user(user))
        cliente.cookies["role"] = papel_antigo
        return cliente.post("/token/refresh/")

    def test_superusuario_com_cookie_antigo_de_admin_vira_plataforma(self):
        """Entrou antes do papel Plataforma existir: o menu mostrava a aba, e o
        middleware do front, lendo "Admin", devolvia ao /fechamentos."""
        dono = User.objects.create_user(username="dono@x.com", password="x", is_superuser=True)
        dono.groups.add(Group.objects.get_or_create(name="Admin")[0])

        resposta = self.renovar(dono)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.cookies["role"].value, "Plataforma")
        self.assertTrue(resposta.cookies["role"]["httponly"])

    def test_quem_mudou_de_cargo_recebe_o_cargo_novo(self):
        promovida = User.objects.create_user(username="ana@x.com", password="x")
        promovida.groups.add(Group.objects.get_or_create(name="Gerente")[0])

        resposta = self.renovar(promovida, papel_antigo="Funcionario")

        self.assertEqual(resposta.cookies["role"].value, "Gerente")

    def test_sem_cargo_o_cookie_fica_como_estava(self):
        """Apagar derrubaria a sessao no meio de uma tela; quem barra e a API."""
        sem_cargo = User.objects.create_user(username="sem@x.com", password="x")

        resposta = self.renovar(sem_cargo)

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn("role", resposta.cookies)
        self.assertIn("access_token", resposta.cookies)
