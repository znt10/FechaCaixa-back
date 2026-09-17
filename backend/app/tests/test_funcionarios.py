"""O funcionario: quem ajuda a conferir o caixa, sem administrar a empresa.

Ele nasceu de uma pergunta pratica — a gerente nao consegue olhar sozinha o
caixa de todas as lojas todo dia. Entao existe um login que ve o painel,
marca turnos como conferidos e corrige um valor errado, mas nao encosta no
que define a empresa: o codigo de acesso, os aparelhos, quem retira dinheiro,
e quem mais tem login.

O nome colide com o "funcionario" que preenche o formulario na loja — esse
nao tem login nenhum, entra pelo codigo da empresa no aparelho. Aqui,
funcionario e sempre o login de conferencia.
"""

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from rest_framework.test import APITestCase

from app.models import Conta, FechamentoCaixa

from .fabricas import criar_loja, lista, vincular_conta


class BaseDoFuncionario(APITestCase):
    def setUp(self):
        cache.clear()
        for nome in ("Admin", "Gerente", "Responsavel", "Funcionario"):
            Group.objects.get_or_create(name=nome)

        self.conta = Conta.objects.create(nome="Aurora Salgados")
        self.outra = Conta.objects.create(nome="Concorrente")

        self.gerente = User.objects.create_user(username="ger@aurora.com", password="x")
        self.gerente.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(self.gerente, self.conta)

        self.funcionario = User.objects.create_user(
            username="conf@aurora.com", email="conf@aurora.com", password="x",
            first_name="Ana",
        )
        self.funcionario.groups.add(Group.objects.get(name="Funcionario"))
        vincular_conta(self.funcionario, self.conta)

        self.loja = criar_loja(
            conta=self.conta, nome_loja="Loja A", cidade="Patos", endereco="Rua 1"
        )


class OQueOFuncionarioPodeTests(BaseDoFuncionario):
    def setUp(self):
        super().setUp()
        self.fechamento = FechamentoCaixa.objects.create(
            loja=self.loja, data="2026-08-20", periodo="MANHA", dinheiro=100
        )
        self.client.force_authenticate(user=self.funcionario)

    def test_ve_o_painel_da_propria_empresa(self):
        resp = self.client.get("/api/v1/fechamentos-caixa/")

        self.assertEqual(resp.status_code, 200)
        dados = lista(resp)
        self.assertEqual(len(dados), 1)

    def test_nao_ve_o_caixa_de_outra_empresa(self):
        loja_vizinha = criar_loja(
            conta=self.outra, nome_loja="Rival", cidade="Patos", endereco="Rua 2"
        )
        FechamentoCaixa.objects.create(
            loja=loja_vizinha, data="2026-08-20", periodo="MANHA", dinheiro=999
        )

        resp = self.client.get("/api/v1/fechamentos-caixa/")

        dados = lista(resp)
        self.assertEqual([d["loja_nome"] for d in dados], ["Loja A"])

    def test_marca_como_conferido_e_fica_o_nome_dele(self):
        resp = self.client.patch(
            f"/api/v1/fechamentos-caixa/{self.fechamento.public_id}/conferir/"
        )

        self.assertEqual(resp.status_code, 200)
        self.fechamento.refresh_from_db()
        self.assertTrue(self.fechamento.conferido)
        self.assertEqual(self.fechamento.conferido_por, self.funcionario)

    def test_corrige_um_valor_errado(self):
        resp = self.client.patch(
            f"/api/v1/fechamentos-caixa/{self.fechamento.public_id}/",
            {"dinheiro": "150.00"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.fechamento.refresh_from_db()
        self.assertEqual(str(self.fechamento.dinheiro), "150.00")

    def test_ve_as_lojas_para_montar_o_painel(self):
        resp = self.client.get("/api/v1/lojas/")

        self.assertEqual(resp.status_code, 200)
        dados = lista(resp)
        self.assertEqual([l["nome_loja"] for l in dados], ["Loja A"])


class OQueOFuncionarioNaoPodeTests(BaseDoFuncionario):
    """A fronteira do cargo. Cada um destes e um jeito de virar gerente."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.funcionario)

    def test_nao_ve_o_codigo_de_acesso(self):
        resp = self.client.get("/api/v1/minha-empresa/")
        self.assertEqual(resp.status_code, 403)

    def test_nao_troca_o_codigo_de_acesso(self):
        resp = self.client.post("/api/v1/minha-empresa/novo-codigo/")
        self.assertEqual(resp.status_code, 403)

    def test_nao_lista_os_aparelhos(self):
        resp = self.client.get("/api/v1/aparelhos/")
        self.assertEqual(resp.status_code, 403)

    def test_nao_cadastra_quem_retira_dinheiro(self):
        resp = self.client.post(
            "/api/v1/responsaveis-retirada/", {"nome": "Eu mesmo"}, format="json"
        )
        self.assertEqual(resp.status_code, 403)

    def test_nao_cria_outro_funcionario(self):
        resp = self.client.post(
            "/api/v1/funcionarios/",
            {"first_name": "Comparsa", "email": "c@x.com", "password": "123456"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_nao_lista_os_funcionarios(self):
        resp = self.client.get("/api/v1/funcionarios/")
        self.assertEqual(resp.status_code, 403)

    def test_nao_cria_loja(self):
        resp = self.client.post(
            "/api/v1/lojas/",
            {"nome_loja": "Minha", "cidade": "Patos", "endereco": "Rua 3"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)


class GerenciarFuncionariosTests(BaseDoFuncionario):
    URL = "/api/v1/funcionarios/"

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.gerente)

    def _payload(self, email="novo@aurora.com"):
        return {"first_name": "Novo", "email": email, "password": "123456"}

    def test_gerente_cria_funcionario(self):
        resp = self.client.post(self.URL, self._payload(), format="json")

        self.assertEqual(resp.status_code, 201)
        criado = User.objects.get(username="novo@aurora.com")
        self.assertEqual(
            list(criado.groups.values_list("name", flat=True)), ["Funcionario"]
        )

    def test_funcionario_novo_nasce_na_empresa_de_quem_criou(self):
        self.client.post(self.URL, self._payload(), format="json")

        criado = User.objects.get(username="novo@aurora.com")
        self.assertEqual(criado.perfil.conta, self.conta)

    def test_quem_cria_nao_escolhe_a_empresa(self):
        payload = self._payload()
        payload["conta"] = str(self.outra.public_id)

        self.client.post(self.URL, payload, format="json")

        self.assertEqual(User.objects.get(username="novo@aurora.com").perfil.conta, self.conta)

    def test_lista_so_os_funcionarios_da_propria_empresa(self):
        vizinho = User.objects.create_user(
            username="conf@rival.com", email="conf@rival.com", password="x"
        )
        vizinho.groups.add(Group.objects.get(name="Funcionario"))
        vincular_conta(vizinho, self.outra)

        resp = self.client.get(self.URL)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual([f["email"] for f in resp.data], ["conf@aurora.com"])

    def test_a_lista_nao_traz_o_proprio_gerente(self):
        """Gerente nao e funcionario: ele nao pode se desativar por engano."""
        resp = self.client.get(self.URL)

        self.assertNotIn("ger@aurora.com", [f["email"] for f in resp.data])

    def test_desativar_impede_o_login_sem_apagar_o_nome(self):
        resp = self.client.patch(
            f"{self.URL}{self.funcionario.id}/", {"ativo": False}, format="json"
        )

        self.assertEqual(resp.status_code, 200)
        self.funcionario.refresh_from_db()
        self.assertFalse(self.funcionario.is_active)
        self.assertTrue(User.objects.filter(id=self.funcionario.id).exists())

    def test_desativado_continua_na_lista_marcado_como_inativo(self):
        self.client.patch(
            f"{self.URL}{self.funcionario.id}/", {"ativo": False}, format="json"
        )

        resp = self.client.get(self.URL)

        self.assertEqual([f["ativo"] for f in resp.data], [False])

    def test_reativar_devolve_o_acesso(self):
        self.funcionario.is_active = False
        self.funcionario.save(update_fields=["is_active"])

        self.client.patch(
            f"{self.URL}{self.funcionario.id}/", {"ativo": True}, format="json"
        )

        self.funcionario.refresh_from_db()
        self.assertTrue(self.funcionario.is_active)

    def test_apagar_remove_o_usuario(self):
        resp = self.client.delete(f"{self.URL}{self.funcionario.id}/")

        self.assertEqual(resp.status_code, 204)
        self.assertFalse(User.objects.filter(id=self.funcionario.id).exists())

    def test_apagar_nao_leva_junto_o_fechamento_que_ele_conferiu(self):
        """conferido_por e SET_NULL: o lancamento sobrevive a saida da pessoa.

        Sem esta garantia, apagar um funcionario apagaria caixa lancado — o
        tipo de perda que so se descobre no fim do mes.
        """
        fechamento = FechamentoCaixa.objects.create(
            loja=self.loja, data="2026-08-20", periodo="MANHA", dinheiro=100,
            conferido=True, conferido_por=self.funcionario,
        )

        self.client.delete(f"{self.URL}{self.funcionario.id}/")

        fechamento.refresh_from_db()
        self.assertIsNone(fechamento.conferido_por)
        self.assertEqual(str(fechamento.dinheiro), "100.00")

    def test_nao_desativa_funcionario_de_outra_empresa(self):
        vizinho = User.objects.create_user(
            username="conf@rival.com", email="conf@rival.com", password="x"
        )
        vizinho.groups.add(Group.objects.get(name="Funcionario"))
        vincular_conta(vizinho, self.outra)

        resp = self.client.patch(
            f"{self.URL}{vizinho.id}/", {"ativo": False}, format="json"
        )

        self.assertEqual(resp.status_code, 404)
        vizinho.refresh_from_db()
        self.assertTrue(vizinho.is_active)

    def test_nao_apaga_a_propria_gerente_pela_rota_de_funcionario(self):
        """O id existe e e da mesma conta — o que barra e o cargo."""
        resp = self.client.delete(f"{self.URL}{self.gerente.id}/")

        self.assertEqual(resp.status_code, 404)
        self.assertTrue(User.objects.filter(id=self.gerente.id).exists())

    def test_email_repetido_nao_cria_login_fantasma(self):
        resp = self.client.post(
            self.URL, self._payload(email="conf@aurora.com"), format="json"
        )

        self.assertEqual(resp.status_code, 400)
