from django.contrib.auth.models import Group, User
from django.test import TestCase
from rest_framework.test import APIClient, APITestCase

from app.models import Loja
from .fabricas import conta_padrao, criar_loja, lista, vincular_conta
from app.models import Conta


class LojaQuerysetEscopoTests(TestCase):
    def setUp(self):
        for nome in ("Admin", "Gerente"):
            Group.objects.get_or_create(name=nome)

        self.admin = User.objects.create_user(username="admin@x.com", password="123456")
        self.admin.groups.add(Group.objects.get(name="Admin"))

        self.gerente = User.objects.create_user(username="ger@x.com", password="123456")
        self.gerente.groups.add(Group.objects.get(name="Gerente"))

        # Os dois sao da mesma conta: o que se testa neste bloco e o alcance
        # de cada cargo, e o limite de visibilidade e a conta.
        for user in (self.admin, self.gerente):
            vincular_conta(user)

        self.loja_dele = criar_loja(
            nome_loja="Loja A", cidade="Patos", endereco="Rua 1",
        )
        self.loja_alheia = criar_loja(
            nome_loja="Loja B", cidade="Patos", endereco="Rua 2",
        )

    def test_admin_ve_todas_as_lojas(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        resp = client.get("/api/v1/lojas/")
        nomes = {loja["nome_loja"] for loja in lista(resp)}
        self.assertEqual(nomes, {"Loja A", "Loja B"})

    def test_gerente_ve_as_lojas_da_propria_conta(self):
        """O limite de visibilidade e a conta.

        Antes da camada de Conta este teste exigia que o gerente so visse as
        lojas em que ele era o `Loja.gerente`. Aquele campo era do Unistock,
        onde varios gerentes dividiam uma rede, e saiu na migracao 0037: aqui
        quem administra a conta ve todas as lojas dela.
        """
        outra_conta = Conta.objects.create(nome="Outro negocio")
        criar_loja(
            nome_loja="Loja de outra conta", cidade="Patos", endereco="Rua 9",
            conta=outra_conta,
        )

        client = APIClient()
        client.force_authenticate(self.gerente)
        resp = client.get("/api/v1/lojas/")

        nomes = {loja["nome_loja"] for loja in lista(resp)}
        self.assertEqual(nomes, {"Loja A", "Loja B"})

    def test_o_cadastro_de_loja_nao_pede_pessoa_nenhuma(self):
        """Substitui os tres testes do campo `gerente` da loja.

        Eles cobriam quem podia definir e trocar o gerente "dono" da loja.
        Nao ha mais o que proteger: gerente, responsavel, email,
        telefone_whatsapp e tipo sairam do modelo. O que resta e a garantia de
        que a API nao volta a aceita-los — se algum reaparecer no serializer,
        o formulario de cadastro volta a pedir dado que ninguem preenche.
        """
        client = APIClient()
        client.force_authenticate(self.admin)

        resp = client.post(
            "/api/v1/lojas/",
            {
                "nome_loja": "Loja C",
                "cidade": "Patos",
                "endereco": "Rua 3",
                "gerente": self.gerente.id,
                "email": "loja@x.com",
                "telefone_whatsapp": "83999999999",
                "tipo": "matriz",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(
            set(resp.data),
            {"id", "nome_loja", "cidade", "endereco", "ativo", "cnpj"},
        )


class LojaDeleteTests(APITestCase):
    """DELETE /api/v1/lojas/<id>/.

    Ja teve um irmao aqui: excluir loja com MovimentacaoEstoque tinha que dar
    409, porque a FK era PROTECT. O modelo saiu com o Unistock, e nada mais
    protege a Loja — quem hoje segura uma loja e o caixa lancado, e essa regra
    tem teste proprio em test_lojas_da_gerencia.py.
    """

    def setUp(self):
        grupo_admin, _ = Group.objects.get_or_create(name="Admin")
        self.admin = User.objects.create_user(username="admin", password="123456")
        self.admin.groups.add(grupo_admin)
        vincular_conta(self.admin)
        self.client.force_authenticate(self.admin)

    def test_permite_excluir_loja_sem_historico(self):
        loja = criar_loja(
            nome_loja="Loja Sem Historico", cidade="Patos", endereco="Rua 2",
        )

        response = self.client.delete(f"/api/v1/lojas/{loja.public_id}/")

        self.assertEqual(response.status_code, 204, getattr(response, "data", None))
        self.assertFalse(Loja.objects.filter(pk=loja.pk).exists())
