"""As listas de cadastro saem inteiras, sem paginacao.

O bug que este arquivo tranca apareceu em producao no dia em que a empresa
cadastrou os 61 funcionarios de verdade. O DRF pagina por padrao em 50
(PAGE_SIZE no settings), e `/encarregados/` e um ViewSet — entao a resposta
vinha cortada no quinquagesimo nome, em ordem alfabetica.

Na tela da empresa isso parecia cosmetico: "nao mostra todo mundo". No
formulario da loja nao era. A lista alimenta o seletor de quem esta lancando
o caixa e a lista de consumo, entao as 11 pessoas do fim do alfabeto nao
conseguiam fechar caixa, e o consumo delas nao entrava no desconto do mes.

Subir o PAGE_SIZE so adiaria: consertaria 61 e quebraria de novo em 51 lojas
ou 101 pessoas. Estas listas sao cadastro de UMA conta — dezenas de linhas,
mostradas inteiras na tela — e nao tem por que chegar em pedacos.
"""

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from rest_framework.test import APIClient, APITestCase

from app.models import Conta, Encarregado, ResponsavelRetirada

from .fabricas import vincular_conta

# Acima do PAGE_SIZE de 50, que e o ponto exato onde a resposta quebrava.
QUANTAS_PESSOAS = 61


class BaseDosCadastros(APITestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.conta = Conta.objects.create(nome="Primavera")

        # Nomes numerados com zero a esquerda para a ordem alfabetica ser
        # previsivel: sem isso "Pessoa 10" viria antes de "Pessoa 9" e o teste
        # nao saberia quem deveria ter sumido.
        for numero in range(1, QUANTAS_PESSOAS + 1):
            Encarregado.objects.create(
                nome=f"Pessoa {numero:03d}", conta=self.conta
            )
            ResponsavelRetirada.objects.create(
                nome=f"Responsavel {numero:03d}", conta=self.conta
            )

    def como_gerente(self):
        Group.objects.get_or_create(name="Gerente")
        gerente = User.objects.create_user(
            username="ger@primavera.com", email="ger@primavera.com", password="x"
        )
        gerente.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(gerente, self.conta)
        self.client.force_authenticate(user=gerente)

    def como_aparelho_da_loja(self):
        """O formulario nao tem login: entra pelo codigo da empresa."""
        self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": self.conta.codigo_acesso, "apelido": "Caixa"},
            format="json",
        )

    def nomes(self, url):
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, resp.data)
        # Sem paginacao a resposta e a lista crua. Se um dia voltar a paginar,
        # este assert e o que avisa — em vez de a tela perder gente calada.
        self.assertIsInstance(resp.data, list, "a resposta voltou paginada")
        return [linha["nome"] for linha in resp.data]


class EncarregadosTests(BaseDosCadastros):
    def test_o_painel_lista_todo_mundo(self):
        self.como_gerente()

        nomes = self.nomes("/api/v1/encarregados/")

        self.assertEqual(len(nomes), QUANTAS_PESSOAS)
        self.assertIn(f"Pessoa {QUANTAS_PESSOAS:03d}", nomes)

    def test_o_formulario_da_loja_lista_todo_mundo(self):
        """A ponta que doi: sem os ultimos nomes, eles nao fecham caixa."""
        self.como_aparelho_da_loja()

        nomes = self.nomes("/api/v1/encarregados/")

        self.assertEqual(len(nomes), QUANTAS_PESSOAS)
        self.assertIn(f"Pessoa {QUANTAS_PESSOAS:03d}", nomes)

    def test_o_isolamento_entre_empresas_continua_de_pe(self):
        """Tirar a paginacao nao pode virar porta para a lista do vizinho."""
        vizinha = Conta.objects.create(nome="Concorrente")
        Encarregado.objects.create(nome="Espiao", conta=vizinha)
        self.como_gerente()

        nomes = self.nomes("/api/v1/encarregados/")

        self.assertNotIn("Espiao", nomes)
        self.assertEqual(len(nomes), QUANTAS_PESSOAS)


class ResponsaveisTests(BaseDosCadastros):
    def test_o_painel_lista_todos(self):
        self.como_gerente()

        nomes = self.nomes("/api/v1/responsaveis-retirada/")

        self.assertEqual(len(nomes), QUANTAS_PESSOAS)

    def test_o_formulario_da_loja_lista_todos(self):
        self.como_aparelho_da_loja()

        nomes = self.nomes("/api/v1/responsaveis-retirada/")

        self.assertEqual(len(nomes), QUANTAS_PESSOAS)
