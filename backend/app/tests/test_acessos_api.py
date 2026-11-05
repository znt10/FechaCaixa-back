from unittest import mock

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase
from rest_framework.test import APIClient

from app.models import Assinatura, Conta
from app.services.acessos import criar_gerente

from .fabricas import vincular_conta

LISTA = "/api/v1/plataforma/acessos/"


class AcessosApiTests(TestCase):
    def setUp(self):
        self.dono = User.objects.create_superuser(username="dono", password="x")
        self.api = APIClient()
        self.api.force_authenticate(self.dono)

    def nova(self, **gerente):
        corpo = {
            "nome": "Nova",
            "modulos": {"notas": True, "banco": False, "comprovantes": True},
            "gerente": {"nome": "Ana", "email": "ana@x.com", **gerente},
        }
        return self.api.post(LISTA, corpo, format="json")

    def test_cria_empresa_com_gerente_e_modulos(self):
        resposta = self.nova()

        self.assertEqual(resposta.status_code, 201, resposta.data)
        conta = Conta.objects.get(nome="Nova")
        self.assertEqual(
            (conta.modulo_notas_ativo, conta.modulo_banco_ativo, conta.modulo_comprovantes_ativo),
            (True, False, True),
        )
        self.assertTrue(Assinatura.objects.filter(conta=conta).exists())
        self.assertEqual(User.objects.get(username="ana@x.com").perfil.conta, conta)
        self.assertIs(resposta.data["email_enviado"], True)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(resposta.data["id"], str(conta.public_id))
        self.assertEqual(resposta.data["gerentes"][0]["situacao"], "AGUARDANDO_SENHA")

    def test_cria_com_senha_nao_manda_email(self):
        resposta = self.nova(senha="segredo1")
        self.assertEqual(resposta.status_code, 201, resposta.data)
        self.assertIsNone(resposta.data["email_enviado"])
        self.assertEqual(mail.outbox, [])
        self.assertEqual(resposta.data["gerentes"][0]["situacao"], "ATIVO")

    def test_email_repetido_nao_deixa_empresa_orfa(self):
        User.objects.create_user(username="ana@x.com", email="ana@x.com", password="x")
        resposta = self.nova()
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Este email ja esta em uso.", str(resposta.data))
        self.assertFalse(Conta.objects.filter(nome="Nova").exists())

    def test_nome_vazio_e_400(self):
        resposta = self.api.post(
            LISTA, {"nome": "  ", "gerente": {"nome": "Ana", "email": "ana@x.com"}}, format="json"
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(Conta.objects.exists())

    def test_smtp_falhando_nao_desfaz(self):
        with mock.patch("app.services.acessos.enviar_email_definir_senha", side_effect=OSError):
            resposta = self.nova()
        self.assertEqual(resposta.status_code, 201, resposta.data)
        self.assertIs(resposta.data["email_enviado"], False)
        self.assertTrue(User.objects.filter(username="ana@x.com").exists())

    def test_lista_mostra_situacoes(self):
        conta = Conta.objects.create(nome="Padaria")
        criar_gerente(conta, "Bia", "bia@x.com", "segredo1")
        criar_gerente(conta, "Caio", "caio@x.com")
        desativado = criar_gerente(conta, "Davi", "davi@x.com", "segredo1")
        desativado.is_active = False
        desativado.save()

        empresa = next(e for e in self.api.get(LISTA).data if e["nome"] == "Padaria")

        self.assertEqual(
            [(g["nome"], g["situacao"]) for g in empresa["gerentes"]],
            [("Bia", "ATIVO"), ("Caio", "AGUARDANDO_SENHA"), ("Davi", "DESATIVADO")],
        )
        self.assertEqual(
            empresa["modulos"],
            {"notas": False, "banco": False, "comprovantes": False, "comprovantes_pessoais": False},
        )

    def test_lista_so_mostra_gerentes(self):
        conta = Conta.objects.create(nome="Padaria")
        funcionario = User.objects.create_user(username="f@x.com", email="f@x.com", password="x")
        funcionario.groups.add(Group.objects.get_or_create(name="Funcionario")[0])
        vincular_conta(funcionario, conta)
        empresa = next(e for e in self.api.get(LISTA).data if e["nome"] == "Padaria")
        self.assertEqual(empresa["gerentes"], [])

    def test_patch_liga_e_desliga_modulo(self):
        conta = Conta.objects.create(nome="Padaria", modulo_notas_ativo=True)
        resposta = self.api.patch(
            f"{LISTA}{conta.public_id}/", {"notas": False, "banco": True}, format="json"
        )
        self.assertEqual(resposta.status_code, 200, resposta.data)
        conta.refresh_from_db()
        self.assertEqual((conta.modulo_notas_ativo, conta.modulo_banco_ativo), (False, True))
        self.assertEqual(resposta.data["modulos"]["banco"], True)

    def test_adiciona_segundo_gerente_na_mesma_empresa(self):
        conta = Conta.objects.create(nome="Padaria")
        criar_gerente(conta, "Bia", "bia@x.com", "segredo1")
        resposta = self.api.post(
            f"{LISTA}{conta.public_id}/gerentes/",
            {"nome": "Caio", "email": "caio@x.com"},
            format="json",
        )
        self.assertEqual(resposta.status_code, 201, resposta.data)
        self.assertIs(resposta.data["email_enviado"], True)
        self.assertEqual(resposta.data["gerente"]["situacao"], "AGUARDANDO_SENHA")
        self.assertEqual(User.objects.filter(perfil__conta=conta).count(), 2)

    def test_reenviar_link(self):
        conta = Conta.objects.create(nome="Padaria")
        outra = Conta.objects.create(nome="Outra")
        aguardando = criar_gerente(conta, "Caio", "caio@x.com")
        ativo = criar_gerente(conta, "Bia", "bia@x.com", "segredo1")
        de_outra = criar_gerente(outra, "Eva", "eva@x.com")

        url = lambda c, u: f"{LISTA}{c.public_id}/gerentes/{u.id}/reenviar-link/"
        resposta = self.api.post(url(conta, aguardando))
        self.assertEqual(resposta.status_code, 200, resposta.data)
        self.assertIs(resposta.data["email_enviado"], True)
        self.assertEqual(len(mail.outbox), 1)

        resposta = self.api.post(url(conta, ativo))
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Este gerente ja tem senha.", str(resposta.data))

        self.assertEqual(self.api.post(url(conta, de_outra)).status_code, 404)

    def test_nao_superusuario_e_403(self):
        conta = Conta.objects.create(nome="Padaria")
        admin = User.objects.create_user(username="adm@x.com", password="x")
        admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        vincular_conta(admin, conta)
        gerente = criar_gerente(conta, "Caio", "caio@x.com")
        api = APIClient()
        api.force_authenticate(admin)

        chamadas = [
            api.get(LISTA),
            api.post(LISTA, {"nome": "X"}, format="json"),
            api.patch(f"{LISTA}{conta.public_id}/", {"notas": True}, format="json"),
            api.post(f"{LISTA}{conta.public_id}/gerentes/", {"nome": "a", "email": "a@x.com"}, format="json"),
            api.post(f"{LISTA}{conta.public_id}/gerentes/{gerente.id}/reenviar-link/"),
        ]
        self.assertEqual([r.status_code for r in chamadas], [403] * 5)


class ComprovantesPessoaisNaAcessosTests(TestCase):
    """A chave do CPF tambem fica na Acessos, embaixo de Comprovantes."""

    def setUp(self):
        self.dono = User.objects.create_superuser(username="dono", password="x")
        self.api = APIClient()
        self.api.force_authenticate(self.dono)

    def test_liga_os_pessoais_pela_acessos(self):
        conta = Conta.objects.create(nome="Padaria", modulo_comprovantes_ativo=True)
        resposta = self.api.patch(
            f"{LISTA}{conta.public_id}/", {"comprovantes_pessoais": True}, format="json"
        )
        self.assertEqual(resposta.status_code, 200, resposta.data)
        conta.refresh_from_db()
        self.assertTrue(conta.comprovantes_pessoais_ativos)
        self.assertIs(resposta.data["modulos"]["comprovantes_pessoais"], True)

    def test_nova_empresa_ja_com_os_pessoais(self):
        resposta = self.api.post(
            LISTA,
            {
                "nome": "Nova",
                "modulos": {"comprovantes": True, "comprovantes_pessoais": True},
                "gerente": {"nome": "Ana", "email": "ana@x.com", "senha": "segredo1"},
            },
            format="json",
        )
        self.assertEqual(resposta.status_code, 201, resposta.data)
        self.assertTrue(Conta.objects.get(nome="Nova").comprovantes_pessoais_ativos)
