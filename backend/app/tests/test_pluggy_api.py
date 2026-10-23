import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from app.models import ConexaoPluggy, Conta, ContaBancaria, Loja
from app.services.pluggy.cifra import cifra_configurada
from app.services.pluggy.cliente import FalhaDaPluggy
from cryptography.fernet import Fernet

from .fabricas import criar_loja, vincular_conta

BASE = "/api/v1/plataforma/pluggy/"
CHAVE = Fernet.generate_key().decode()
SEM_CHAVE = "Defina PLUGGY_CHAVE_CIFRA no servidor antes de ligar a Pluggy."


@override_settings(PLUGGY_CHAVE_CIFRA=CHAVE, PLUGGY_EMAIL_AVISO="dono@fechacaixa.io")
class PluggyApiTests(TestCase):
    def setUp(self):
        self.dono = User.objects.create_superuser(username="dono", password="x")
        self.api = APIClient()
        self.api.force_authenticate(self.dono)
        self.empresa = Conta.objects.create(nome="Padaria Sol")
        self.loja = criar_loja(conta=self.empresa, nome_loja="Centro")
        self.cb = ContaBancaria.objects.create(
            loja=self.loja, banco="SANTANDER", numero="1234567890"
        )

    def conexao(self, **campos):
        campos.setdefault("email", "a@b.com")
        return ConexaoPluggy.objects.create(conta=self.empresa, **campos)

    def test_so_o_superusuario_entra(self):
        gerente = User.objects.create_user(username="g", password="x")
        vincular_conta(gerente, self.empresa)
        api = APIClient()
        api.force_authenticate(gerente)
        self.assertEqual(api.get(BASE).status_code, 403)
        self.assertEqual(
            api.post(BASE, {"empresa_id": str(self.empresa.public_id), "email": "a@b.com"}).status_code,
            403,
        )

    def test_criar_liga_o_modulo_de_banco(self):
        resp = self.api.post(
            BASE, {"empresa_id": str(self.empresa.public_id), "email": "a@b.com"}, format="json"
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["estado"], "CONFIGURANDO")
        self.assertEqual(resp.json()["empresa"]["nome"], "Padaria Sol")
        self.empresa.refresh_from_db()
        self.assertTrue(self.empresa.modulo_banco_ativo)

    def test_segredo_nunca_volta_nem_no_patch_nem_no_painel(self):
        conexao = self.conexao()
        resp = self.api.patch(
            f"{BASE}{conexao.public_id}/",
            {"client_id": "cid", "client_secret": "segredo-xyz", "item_ids": "i1"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("segredo-xyz", json.dumps(resp.json()))
        self.assertNotIn("cifrado", json.dumps(resp.json()))
        self.assertIsNotNone(resp.json()["segredo_salvo_em"])
        painel = self.api.get(BASE)
        self.assertNotIn("segredo-xyz", json.dumps(painel.json()))
        conexao.refresh_from_db()
        self.assertNotIn("segredo-xyz", conexao.client_secret_cifrado)
        self.assertNotEqual(conexao.client_secret_cifrado, "")

    def test_patch_so_aceita_ativa_e_desligada(self):
        conexao = self.conexao()
        url = f"{BASE}{conexao.public_id}/"
        self.assertEqual(self.api.patch(url, {"estado": "COM_ERRO"}, format="json").status_code, 400)
        resp = self.api.patch(url, {"estado": "DESLIGADA"}, format="json")
        self.assertEqual(resp.json()["estado"], "DESLIGADA")

    def test_testar_sugere_a_conta_pelo_numero(self):
        conexao = self.conexao(client_id="cid", item_ids="i1")
        conexao.guardar_segredo("s")
        conexao.save()
        with patch("app.services.pluggy.painel.ClientePluggy") as cls:
            cls.return_value.contas.return_value = [
                {"id": "acc-1", "number": "123456789-0", "name": "Conta"},
                {"id": "acc-2", "number": "111-1", "name": "Outra"},
            ]
            resp = self.api.post(f"{BASE}{conexao.public_id}/testar/")
        self.assertEqual(resp.status_code, 200)
        contas = resp.json()["contas"]
        self.assertEqual(contas[0]["pluggy_account_id"], "acc-1")
        self.assertEqual(contas[0]["conta_bancaria_sugerida"], str(self.cb.public_id))
        self.assertIsNone(contas[1]["conta_bancaria_sugerida"])
        self.assertEqual(resp.json()["contas_da_empresa"][0]["loja"], "Centro")

    def test_vincular_conta_de_outra_empresa_da_400(self):
        outra = Conta.objects.create(nome="Outra")
        loja = Loja.objects.create(conta=outra, nome_loja="X", cidade="c", endereco="e")
        estranha = ContaBancaria.objects.create(loja=loja, banco="ITAU", numero="9")
        conexao = self.conexao()
        resp = self.api.post(
            f"{BASE}{conexao.public_id}/vincular/",
            {"vinculos": [{"pluggy_account_id": "acc-1", "conta_bancaria_id": str(estranha.public_id)}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Esta conta bancária é de outra empresa.", json.dumps(resp.json(), ensure_ascii=False))
        estranha.refresh_from_db()
        self.assertIsNone(estranha.pluggy_account_id)

    def test_vincular_ativa_e_busca_na_hora(self):
        conexao = self.conexao(client_id="cid", item_ids="i1")
        conexao.guardar_segredo("s")
        conexao.save()
        cliente = MagicMock()
        cliente.item.return_value = {"status": "UPDATED", "lastUpdatedAt": timezone.now().isoformat()}
        cliente.transacoes.return_value = []
        with patch("app.services.pluggy.busca.ClientePluggy", return_value=cliente):
            resp = self.api.post(
                f"{BASE}{conexao.public_id}/vincular/",
                {"vinculos": [{"pluggy_account_id": "acc-1", "conta_bancaria_id": str(self.cb.public_id)}]},
                format="json",
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.cb.refresh_from_db()
        self.assertEqual(self.cb.pluggy_account_id, "acc-1")
        self.assertEqual(self.cb.conexao_pluggy_id, conexao.id)
        conexao.refresh_from_db()
        self.assertEqual(conexao.estado, "ATIVA")
        self.assertIsNotNone(conexao.ultima_busca_ok_em)
        cliente.transacoes.assert_called_once()
        self.assertEqual(resp.json()["estado"], "ATIVA")

    def test_buscar_roda_na_hora(self):
        conexao = self.conexao(client_id="cid", item_ids="i1", estado="ATIVA")
        conexao.guardar_segredo("s")
        conexao.save()
        cliente = MagicMock()
        cliente.item.return_value = {"status": "UPDATED", "lastUpdatedAt": timezone.now().isoformat()}
        with patch("app.services.pluggy.busca.ClientePluggy", return_value=cliente):
            resp = self.api.post(f"{BASE}{conexao.public_id}/buscar/")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.json()["ultima_busca_ok_em"])

    def test_sem_chave_de_cifra_da_400_com_mensagem(self):
        conexao = self.conexao()
        with override_settings(PLUGGY_CHAVE_CIFRA=""):
            self.assertFalse(cifra_configurada())
            r1 = self.api.post(
                BASE, {"empresa_id": str(self.empresa.public_id), "email": "a@b.com"}, format="json"
            )
            r2 = self.api.patch(
                f"{BASE}{conexao.public_id}/", {"client_secret": "x"}, format="json"
            )
        for resp in (r1, r2):
            self.assertEqual(resp.status_code, 400)
            self.assertIn(SEM_CHAVE, json.dumps(resp.json(), ensure_ascii=False))

    def test_contadores(self):
        self.conexao(estado="ATIVA")
        self.conexao(estado="COM_ERRO")
        self.conexao(estado="CONFIGURANDO")
        self.conexao(estado="DESLIGADA")
        painel = self.api.get(BASE).json()
        self.assertEqual(
            painel["contadores"], {"funcionando": 1, "caidas": 1, "configurando": 1}
        )
        self.assertEqual(len(painel["conexoes"]), 4)

    def test_conta_bancaria_diz_a_origem(self):
        Group.objects.get_or_create(name="Gerente")
        self.empresa.modulo_banco_ativo = True
        self.empresa.save()
        gerente = User.objects.create_user(username="g", password="x")
        gerente.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(gerente, self.empresa)
        agora = timezone.now() - timedelta(hours=1)
        conexao = self.conexao(estado="ATIVA", ultima_busca_ok_em=agora)
        outra = ContaBancaria.objects.create(loja=self.loja, banco="ITAU", numero="7")
        ContaBancaria.objects.filter(pk=self.cb.pk).update(
            pluggy_account_id="acc-1", conexao_pluggy=conexao
        )
        api = APIClient()
        api.force_authenticate(gerente)
        resp = api.get("/api/v1/contas-bancarias/")
        self.assertEqual(resp.status_code, 200, resp.content)
        por_numero = {c["numero"]: c for c in resp.json()}
        self.assertEqual(por_numero["1234567890"]["origem"], "PLUGGY")
        self.assertIsNotNone(por_numero["1234567890"]["atualizado_em"])
        self.assertEqual(por_numero["7"]["origem"], "ARQUIVO")
        self.assertIsNone(por_numero["7"]["atualizado_em"])
        conexao.estado = "DESLIGADA"
        conexao.save()
        resp = api.get("/api/v1/contas-bancarias/")
        self.assertEqual({c["origem"] for c in resp.json()}, {"ARQUIVO"})

    def _testar(self, conexao):
        resp = self.api.post(f"{BASE}{conexao.public_id}/testar/")
        return resp.status_code, json.dumps(resp.json(), ensure_ascii=False)

    def test_testar_com_segredo_ilegivel_da_400(self):
        conexao = self.conexao(client_id="cid", item_ids="i1", client_secret_cifrado="lixo")
        status, corpo = self._testar(conexao)
        self.assertEqual(status, 400)
        self.assertIn(
            "Não foi possível ler o Client Secret salvo. Cadastre de novo na Plataforma.", corpo
        )

    def test_testar_sem_chave_de_cifra_da_400(self):
        conexao = self.conexao(client_id="cid", item_ids="i1", client_secret_cifrado="lixo")
        with override_settings(PLUGGY_CHAVE_CIFRA=""):
            status, corpo = self._testar(conexao)
        self.assertEqual(status, 400)
        self.assertIn(SEM_CHAVE, corpo)

    def test_testar_com_falha_da_pluggy_da_400(self):
        conexao = self.conexao(client_id="cid", item_ids="i1")
        conexao.guardar_segredo("s")
        conexao.save()
        with patch("app.services.pluggy.painel.ClientePluggy") as cls:
            cls.return_value.contas.side_effect = FalhaDaPluggy(
                401, "A Pluggy recusou as chaves desta conexão."
            )
            status, corpo = self._testar(conexao)
        self.assertEqual(status, 400)
        self.assertIn("A Pluggy recusou as chaves desta conexão.", corpo)

    def test_buscar_so_em_conexao_ativa_ou_com_erro(self):
        """Buscar em conexao configurando mandaria o e-mail de conectada."""
        for estado in ("CONFIGURANDO", "DESLIGADA"):
            with self.subTest(estado=estado):
                conexao = self.conexao(client_id="cid", item_ids="i1", estado=estado)
                with patch("app.services.pluggy.busca.ClientePluggy") as cls:
                    resp = self.api.post(f"{BASE}{conexao.public_id}/buscar/")
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json(), {"detail": "Ative a conexão antes de buscar."})
                cls.assert_not_called()
                conexao.refresh_from_db()
                self.assertEqual(conexao.estado, estado)

    def test_patch_nao_desfaz_o_que_a_busca_gravou(self):
        """O PATCH leu a conexao ativa; antes de salvar, a busca a derrubou."""
        conexao = self.conexao(estado="ATIVA")
        lida_antes = ConexaoPluggy.objects.select_related("conta").get(pk=conexao.pk)
        ConexaoPluggy.objects.filter(pk=conexao.pk).update(
            estado="COM_ERRO", ultimo_erro="caiu", falhas_seguidas=3
        )

        with patch("app.api.v1.views.pluggy._conexao", return_value=lida_antes):
            resp = self.api.patch(
                f"{BASE}{conexao.public_id}/", {"email": "novo@b.com"}, format="json"
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        conexao.refresh_from_db()
        self.assertEqual(conexao.email, "novo@b.com")
        self.assertEqual(
            (conexao.estado, conexao.ultimo_erro, conexao.falhas_seguidas),
            ("COM_ERRO", "caiu", 3),
        )
        self.assertEqual(resp.json()["estado"], "COM_ERRO")
