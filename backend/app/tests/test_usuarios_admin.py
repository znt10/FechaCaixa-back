from django.contrib.auth.models import Group, User
from django.test import TestCase
from rest_framework.test import APIClient

from app.models import Conta, PerfilUsuario
from .fabricas import criar_loja, lista, vincular_conta


class RegistrarGerenteTests(TestCase):
    """Quem pode criar um gerente — e em qual empresa ele nasce.

    Criar gerente e do Admin. O gerente cria FUNCIONARIO (ver
    test_funcionarios.py): alguem que confere o caixa e nao administra a
    empresa. Criar um par com os mesmos poderes que os seus e outra decisao.

    O que mudou aqui foi o vinculo: quem e criado por alguem que tem empresa
    nasce dentro dela. Antes o login era criado solto, entrava no sistema e
    nao enxergava loja nenhuma — parecia conta quebrada, e era falta de
    perfil.
    """

    def setUp(self):
        for nome in ("Admin", "Gerente", "Funcionario"):
            Group.objects.get_or_create(name=nome)
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        self.admin = User.objects.create_user(username="admin@x.com", password="123456")
        self.admin.groups.add(Group.objects.get(name="Admin"))
        self.gerente = User.objects.create_user(username="ger@x.com", password="123456")
        self.gerente.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(self.gerente, self.conta)

    def _payload(self, email="novoger@x.com"):
        return {
            "first_name": "Novo Gerente",
            "email": email,
            "password": "123456",
            "tipo_usuario": "gerente",
        }

    def test_admin_cria_gerente(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        resp = client.post("/api/v1/user/registrar/", self._payload(), format="json")
        self.assertEqual(resp.status_code, 201)

    def test_gerente_nao_cria_outro_gerente(self):
        """Ele cria funcionario, nao um par. Ver test_funcionarios.py."""
        client = APIClient()
        client.force_authenticate(self.gerente)
        resp = client.post("/api/v1/user/registrar/", self._payload(), format="json")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(User.objects.filter(username="novoger@x.com").exists())

    def test_gerente_novo_nasce_na_empresa_de_quem_criou(self):
        """Quando quem cria tem empresa, o login novo nasce dentro dela."""
        admin_da_empresa = User.objects.create_user(
            username="dono@x.com", password="123456"
        )
        admin_da_empresa.groups.add(Group.objects.get(name="Admin"))
        vincular_conta(admin_da_empresa, self.conta)
        client = APIClient()
        client.force_authenticate(admin_da_empresa)

        client.post("/api/v1/user/registrar/", self._payload(), format="json")

        novo = User.objects.get(username="novoger@x.com")
        self.assertEqual(novo.perfil.conta, self.conta)

    def test_quem_cria_nao_escolhe_a_empresa(self):
        """A conta vem do login de quem cria, nunca do corpo do request."""
        outra = Conta.objects.create(nome="Concorrente")
        admin_da_empresa = User.objects.create_user(
            username="dono@x.com", password="123456"
        )
        admin_da_empresa.groups.add(Group.objects.get(name="Admin"))
        vincular_conta(admin_da_empresa, self.conta)
        client = APIClient()
        client.force_authenticate(admin_da_empresa)

        payload = self._payload()
        payload["conta"] = str(outra.public_id)
        client.post("/api/v1/user/registrar/", payload, format="json")

        novo = User.objects.get(username="novoger@x.com")
        self.assertEqual(novo.perfil.conta, self.conta)

    def test_funcionario_nao_cria_gerente(self):
        """Era o "Responsavel", o login proprio da loja — cargo que nao existe
        mais. Quem esta abaixo da gerencia hoje e o Funcionario: confere o
        caixa, e nao administra a empresa."""
        funcionario = User.objects.create_user(username="loja@x.com", password="123456")
        funcionario.groups.add(Group.objects.get(name="Funcionario"))
        vincular_conta(funcionario, self.conta)
        client = APIClient()
        client.force_authenticate(funcionario)

        resp = client.post("/api/v1/user/registrar/", self._payload(), format="json")

        self.assertEqual(resp.status_code, 403)

    def test_sem_login_nao_cria_gerente(self):
        resp = APIClient().post(
            "/api/v1/user/registrar/", self._payload(), format="json"
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(User.objects.filter(username="novoger@x.com").exists())

    def test_superuser_sem_empresa_cria_gerente_sem_perfil(self):
        """O dono da plataforma nao tem conta: nao ha empresa para herdar.

        Fica explicito aqui porque e o unico caso em que o gerente nasce sem
        vinculo — e alguem precisa liga-lo a uma conta depois.
        """
        client = APIClient()
        client.force_authenticate(self.admin)

        client.post("/api/v1/user/registrar/", self._payload(), format="json")

        novo = User.objects.get(username="novoger@x.com")
        self.assertFalse(PerfilUsuario.objects.filter(user=novo).exists())


class ListaDeUsuariosTests(TestCase):
    """Quem cada cargo enxerga em /api/v1/user/.

    Era EstruturaTests, sobre /api/v1/user/estrutura/ — a arvore
    gerente > lojas > responsaveis de cada loja. A rota saiu junto com o login
    proprio da loja: nao existe mais "responsavel da loja" para pendurar na
    arvore, e as lojas de uma empresa nao se dividem por gerente. Quem lista
    as pessoas da empresa hoje e /funcionarios/, que escopa por conta.

    O que continua valendo — e por isso ficou — e o corte: gerente nao lista
    o sistema inteiro.
    """

    def setUp(self):
        for nome in ("Admin", "Gerente"):
            Group.objects.get_or_create(name=nome)
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        self.outra = Conta.objects.create(nome="Concorrente")

        self.admin = User.objects.create_user(username="admin@x.com", password="123456")
        self.admin.groups.add(Group.objects.get(name="Admin"))
        self.admin.is_superuser = True
        self.admin.save(update_fields=["is_superuser"])

        self.gerente = User.objects.create_user(
            username="ger@x.com", password="123456", first_name="Zeca"
        )
        self.gerente.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(self.gerente, self.conta)
        criar_loja(conta=self.conta, nome_loja="Loja A", cidade="Patos", endereco="Rua 1")

        self.vizinho = User.objects.create_user(
            username="ger@concorrente.com", password="123456", first_name="Rival"
        )
        self.vizinho.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(self.vizinho, self.outra)
        criar_loja(
            conta=self.outra, nome_loja="Loja do Rival", cidade="Patos",
            endereco="Rua 2",
        )

    def test_admin_lista_todos_os_usuarios(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        resp = client.get("/api/v1/user/")

        ids = {u["id"] for u in lista(resp)}
        self.assertEqual(ids, {self.admin.id, self.gerente.id, self.vizinho.id})

    def test_gerente_nao_lista_todos_os_usuarios(self):
        client = APIClient()
        client.force_authenticate(self.gerente)
        resp = client.get("/api/v1/user/")

        ids = {u["id"] for u in lista(resp)}
        # Gerente ve so a si mesmo: nem o admin, nem a gerente da empresa
        # vizinha. Os funcionarios dele saem por /funcionarios/.
        self.assertEqual(ids, {self.gerente.id})

    def test_gerente_nao_enxerga_a_empresa_vizinha(self):
        client = APIClient()
        client.force_authenticate(self.gerente)
        resp = client.get("/api/v1/lojas/")

        nomes = {loja["nome_loja"] for loja in lista(resp)}
        self.assertEqual(nomes, {"Loja A"})
