from io import BytesIO

from django.contrib.auth.models import Group, User
from openpyxl import load_workbook
from rest_framework.test import APIClient

from app.models import CategoriaDeSalgado, Conta, FechamentoCaixa, Salgado, Venda

from .fabricas import DIA_COMUM, criar_loja
from .test_desperdicio import BaseDoDesperdicio


class BaseDasVendas(BaseDoDesperdicio):
    """O cenario do desperdicio, com uma familia de bolos marcada para contar
    vendas. Os salgados ficam desmarcados, como na loja que pediu isto."""

    def setUp(self):
        super().setUp()
        self.bolos = CategoriaDeSalgado.objects.create(
            conta=self.conta, nome="Bolos", conta_vendas=True
        )
        self.formigueiro = Salgado.objects.create(
            categoria=self.bolos, nome="Formigueiro"
        )
        self.nata = Salgado.objects.create(categoria=self.bolos, nome="Nata")

    def enviar(self, vendas):
        return self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(vendas=vendas),
            format="json",
        )


class VendasNoEnvioTests(BaseDasVendas):
    def test_lanca_as_vendas_do_turno(self):
        resp = self.enviar([
            {"salgado": str(self.formigueiro.public_id), "quantidade": 10},
            {"salgado": str(self.nata.public_id), "quantidade": 10},
        ])

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(Venda.objects.count(), 2)

    def test_a_venda_nao_mexe_no_total(self):
        """O dinheiro da venda ja entrou pelo PIX, cartao e dinheiro."""
        resp = self.enviar([
            {"salgado": str(self.formigueiro.public_id), "quantidade": 30}
        ])

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(str(resp.data["total"]), "150.00")

    def test_item_de_categoria_nao_marcada_e_recusado(self):
        """A coxinha esta no catalogo, mas a loja nao conta venda de salgado."""
        resp = self.enviar([
            {"salgado": str(self.coxinha.public_id), "quantidade": 5}
        ])

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(FechamentoCaixa.objects.count(), 0)

    def test_quantidade_zero_e_recusada(self):
        resp = self.enviar([
            {"salgado": str(self.nata.public_id), "quantidade": 0}
        ])

        self.assertEqual(resp.status_code, 400)

    def test_item_de_outra_empresa_e_recusado(self):
        vizinha = Conta.objects.create(nome="Vizinha")
        bolos = CategoriaDeSalgado.objects.create(
            conta=vizinha, nome="Bolos", conta_vendas=True
        )
        cenoura = Salgado.objects.create(categoria=bolos, nome="Cenoura")

        resp = self.enviar([
            {"salgado": str(cenoura.public_id), "quantidade": 2}
        ])

        self.assertEqual(resp.status_code, 400)

    def test_o_desperdicio_continua_aceitando_o_catalogo_inteiro(self):
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(desperdicios=[
                {"salgado": str(self.coxinha.public_id), "quantidade": 2},
                {"salgado": str(self.nata.public_id), "quantidade": 1},
            ]),
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.data)

    def test_corrigir_o_pix_nao_apaga_as_vendas(self):
        resp = self.enviar([
            {"salgado": str(self.nata.public_id), "quantidade": 4}
        ])
        payload = self.payload(pix="120.00")

        resp = self.client.patch(
            f"/api/v1/fechamentos-caixa/{resp.data['id']}/correcao/",
            payload,
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Venda.objects.count(), 1)

    def test_lista_vazia_apaga_as_vendas(self):
        resp = self.enviar([
            {"salgado": str(self.nata.public_id), "quantidade": 4}
        ])

        resp = self.client.patch(
            f"/api/v1/fechamentos-caixa/{resp.data['id']}/correcao/",
            self.payload(vendas=[]),
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Venda.objects.count(), 0)


class ConfiguracaoDasVendasTests(BaseDasVendas):
    def test_o_formulario_recebe_a_chave_desligada_por_padrao(self):
        resp = self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": self.conta.codigo_acesso},
            format="json",
        )

        self.assertIs(resp.data["empresa"]["vendas_ativas"], False)

    def test_o_catalogo_diz_quais_categorias_contam_vendas(self):
        resp = self.client.get("/api/v1/salgados/")
        linhas = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        por_nome = {(s["categoria_nome"], s["nome"]): s for s in linhas}

        self.assertIs(por_nome[("Bolos", "Nata")]["categoria_conta_vendas"], True)
        self.assertIs(
            por_nome[("Salgados grande", "Coxinha")]["categoria_conta_vendas"], False
        )


class VendasNoPainelTests(BaseDasVendas):
    def setUp(self):
        super().setUp()
        fechamento = FechamentoCaixa.objects.create(
            loja=self.loja, nome_funcionario="Ana", data=DIA_COMUM, periodo="TARDE"
        )
        Venda.objects.create(fechamento=fechamento, salgado=self.nata, quantidade=10)
        self.gerente = User.objects.create_user(
            username="gerente@marina.com", password="x"
        )
        Group.objects.get_or_create(name="Gerente")[0].user_set.add(self.gerente)
        from .fabricas import vincular_conta

        vincular_conta(self.gerente, self.conta)
        self.client = APIClient()
        self.client.force_authenticate(self.gerente)

    def test_o_painel_recebe_as_vendas_aninhadas(self):
        resp = self.client.get(
            "/api/v1/fechamentos-caixa/"
        )
        linhas = resp.data["results"] if isinstance(resp.data, dict) else resp.data

        venda = linhas[0]["vendas"][0]
        self.assertEqual(venda["nome"], "Nata")
        self.assertEqual(venda["categoria_nome"], "Bolos")
        self.assertEqual(venda["quantidade"], 10)

    def test_a_planilha_tem_a_aba_de_vendas(self):
        resp = self.client.get(f"/api/v1/planilha/?de={DIA_COMUM}&ate={DIA_COMUM}")
        folha = load_workbook(BytesIO(resp.content))["Vendas"]
        linhas = list(folha.values)

        self.assertEqual(linhas[1][3:6], ("Bolos", "Nata", 10))
