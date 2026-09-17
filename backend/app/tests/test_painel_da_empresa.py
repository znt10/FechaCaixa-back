"""A tela de admin do FechaCaixa: a empresa administrando a si mesma.

Ate agora, mudar o codigo de acesso, ver quais aparelhos estao conectados ou
cadastrar quem retira dinheiro so dava pelo /admin/ do Django — ou seja, com
acesso de dono da plataforma. Estes endpoints existem para que o dono da
empresa faca isso sozinho, e enxergando so a propria conta.
"""

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase

from app.models import Conta, DispositivoDoFormulario

from .fabricas import criar_loja, vincular_conta


class MinhaEmpresaTests(APITestCase):
    URL = "/api/v1/minha-empresa/"

    def setUp(self):
        cache.clear()
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        self.outra = Conta.objects.create(nome="Concorrente")
        self.admin = User.objects.create_user(
            username="dona@aurora.com", email="dona@aurora.com", password="x"
        )
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        vincular_conta(self.admin, self.conta)
        self.client.force_authenticate(user=self.admin)

    def test_admin_ve_o_codigo_da_propria_empresa(self):
        resp = self.client.get(self.URL)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["nome"], "Aurora Salgados")
        self.assertEqual(resp.data["codigo_acesso"], self.conta.codigo_acesso)
        self.assertEqual(resp.data["fechamentos_por_dia"], 2)

    def test_nunca_devolve_a_empresa_de_outro(self):
        """A conta vem do login, nunca de um parametro."""
        resp = self.client.get(f"{self.URL}?conta={self.outra.slug}")

        self.assertEqual(resp.data["nome"], "Aurora Salgados")

    def test_muda_para_um_fechamento_por_dia(self):
        resp = self.client.patch(self.URL, {"fechamentos_por_dia": 1}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.conta.refresh_from_db()
        self.assertEqual(self.conta.fechamentos_por_dia, 1)

    def test_nao_deixa_escrever_o_codigo_a_mao(self):
        """Trocar o codigo e uma acao com consequencia (derruba os aparelhos),
        e nao a edicao de um campo de texto."""
        codigo_antigo = self.conta.codigo_acesso

        self.client.patch(self.URL, {"codigo_acesso": "HACK-0001"}, format="json")

        self.conta.refresh_from_db()
        self.assertEqual(self.conta.codigo_acesso, codigo_antigo)

    def test_sem_login_nao_responde(self):
        self.client.force_authenticate(user=None)

        resp = self.client.get(self.URL)

        self.assertEqual(resp.status_code, 401)

    def test_gerente_administra_a_propria_empresa(self):
        """Mudanca de produto: esta tela passou a ser a do gerente.

        Antes ela era exclusiva do Admin, com o argumento de que trocar o
        codigo e derrubar aparelho e decisao de dono. Na pratica quem esta na
        loja quando o celular novo precisa entrar e o gerente — e ele ja
        enxerga a operacao inteira da conta dele.
        """
        gerente = User.objects.create_user(username="g@aurora.com", password="x")
        gerente.groups.add(Group.objects.get_or_create(name="Gerente")[0])
        vincular_conta(gerente, self.conta)
        self.client.force_authenticate(user=gerente)

        resp = self.client.get(self.URL)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["codigo_acesso"], self.conta.codigo_acesso)

    def test_gerente_de_outra_empresa_ve_a_dele(self):
        """O acesso do gerente nao virou acesso a qualquer empresa."""
        vizinho = User.objects.create_user(username="g@concorrente.com", password="x")
        vizinho.groups.add(Group.objects.get_or_create(name="Gerente")[0])
        vincular_conta(vizinho, self.outra)
        self.client.force_authenticate(user=vizinho)

        resp = self.client.get(self.URL)

        self.assertEqual(resp.data["nome"], "Concorrente")
        self.assertNotEqual(resp.data["codigo_acesso"], self.conta.codigo_acesso)

    def test_responsavel_de_loja_nao_administra_a_empresa(self):
        """Quem lanca o caixa nao troca o codigo que libera os aparelhos."""
        responsavel = User.objects.create_user(username="loja@aurora.com", password="x")
        responsavel.groups.add(Group.objects.get_or_create(name="Responsavel")[0])
        vincular_conta(responsavel, self.conta)
        self.client.force_authenticate(user=responsavel)

        resp = self.client.get(self.URL)

        self.assertEqual(resp.status_code, 403)


class NovoCodigoTests(APITestCase):
    URL = "/api/v1/minha-empresa/novo-codigo/"

    def setUp(self):
        cache.clear()
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        self.admin = User.objects.create_user(username="dona@aurora.com", password="x")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        vincular_conta(self.admin, self.conta)
        self.client.force_authenticate(user=self.admin)

    def test_gerar_novo_codigo_troca_o_codigo(self):
        antigo = self.conta.codigo_acesso

        resp = self.client.post(self.URL)

        self.assertEqual(resp.status_code, 200)
        self.conta.refresh_from_db()
        self.assertNotEqual(self.conta.codigo_acesso, antigo)
        self.assertEqual(resp.data["codigo_acesso"], self.conta.codigo_acesso)

    def test_gerar_novo_codigo_derruba_os_aparelhos(self):
        """E a razao de existir do botao: o codigo vazou, e continuar aceitando
        os aparelhos que entraram com ele seria trocar a fechadura deixando as
        copias antigas funcionando."""
        DispositivoDoFormulario.objects.create(
            conta=self.conta, apelido="Celular", token_hash="a" * 64
        )

        self.client.post(self.URL)

        dispositivo = DispositivoDoFormulario.objects.get()
        self.assertIsNotNone(dispositivo.revogado_em)


class AparelhosDaEmpresaTests(APITestCase):
    URL = "/api/v1/aparelhos/"

    def setUp(self):
        cache.clear()
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        self.outra = Conta.objects.create(nome="Concorrente")
        self.meu = DispositivoDoFormulario.objects.create(
            conta=self.conta, apelido="Celular do balcao", token_hash="a" * 64
        )
        self.do_vizinho = DispositivoDoFormulario.objects.create(
            conta=self.outra, apelido="Celular rival", token_hash="b" * 64
        )
        self.admin = User.objects.create_user(username="dona@aurora.com", password="x")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        vincular_conta(self.admin, self.conta)
        self.client.force_authenticate(user=self.admin)

    def test_lista_so_os_aparelhos_da_propria_empresa(self):
        resp = self.client.get(self.URL)

        apelidos = [a["apelido"] for a in resp.data]
        self.assertEqual(apelidos, ["Celular do balcao"])

    def test_desconectar_revoga_o_aparelho(self):
        resp = self.client.post(f"{self.URL}{self.meu.public_id}/desconectar/")

        self.assertEqual(resp.status_code, 200)
        self.meu.refresh_from_db()
        self.assertIsNotNone(self.meu.revogado_em)

    def test_nao_desconecta_aparelho_de_outra_empresa(self):
        resp = self.client.post(f"{self.URL}{self.do_vizinho.public_id}/desconectar/")

        self.assertEqual(resp.status_code, 404)
        self.do_vizinho.refresh_from_db()
        self.assertIsNone(self.do_vizinho.revogado_em)

    def test_aparelho_revogado_sai_da_lista(self):
        self.meu.revogado_em = timezone.now()
        self.meu.save(update_fields=["revogado_em"])

        resp = self.client.get(self.URL)

        self.assertEqual(resp.data, [])
