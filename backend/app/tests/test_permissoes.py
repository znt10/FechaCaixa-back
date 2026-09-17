"""Trava o comportamento das regras de acesso, agora unificadas.

Escritos ANTES do refactor, quando "essa pessoa e gerente ou admin?" estava em
tres copias (permissions.py, api/v1/viewsets.py, notifications/__init__.py) e
"qual o papel dela?" em duas (views.py, api/v1/viewsets.py). O papel deles era
registrar o que cada copia respondia, para que a unificacao nao mudasse regra
de acesso sem ninguem notar.

Hoje a regra vive so em app/permissions.py. Os testes seguem valendo por dois
motivos: eles descrevem o acesso que o sistema concede (que continua sendo o
mesmo de antes), e barram alguem reintroduzir uma copia local no futuro.

Se um destes ficar vermelho, a pergunta certa nao e "conserta o teste", e sim
"essa mudanca de acesso era intencional?".
"""

from django.contrib.auth.models import AnonymousUser, Group, User
from django.test import RequestFactory, TestCase

from app import notifications
from app.api.v1.views import get_user_group_name as papel_do_viewsets
from app.api.v1.views import is_gerente_ou_admin as regra_do_viewsets
from app.models import Loja
from app.permissions import (
    IsGerenteOrAdministrador,
    get_user_group_name,
    is_gerente_ou_admin,
)
from app.views import get_user_group_name as papel_das_views
from .fabricas import criar_loja


def criar_grupos():
    for nome in ("Admin", "Gerente", "Responsavel"):
        Group.objects.get_or_create(name=nome)


def usuario(username, grupo=None):
    user = User.objects.create_user(username=username, password="123456")
    if grupo:
        user.groups.add(Group.objects.get(name=grupo))
    return user


def requisicao(metodo, user):
    request = getattr(RequestFactory(), metodo)("/")
    request.user = user
    return request


class RegraGerenteOuAdminTests(TestCase):
    """Todos os pontos de chamada respondem a mesma pergunta, igual."""

    def setUp(self):
        criar_grupos()
        self.superuser = User.objects.create_superuser(
            "root@email.com", password="123456"
        )
        self.admin = usuario("admin@email.com", "Admin")
        self.gerente = usuario("ger@email.com", "Gerente")
        self.responsavel = usuario("resp@email.com", "Responsavel")
        self.sem_grupo = usuario("nada@email.com")

    def implementacoes(self, user):
        """Cada ponto de chamada, na sua interface, para o mesmo usuario."""
        return {
            "permissions.is_gerente_ou_admin": is_gerente_ou_admin(user),
            "viewsets.is_gerente_ou_admin": regra_do_viewsets(user),
            "permissions.IsGerenteOrAdministrador": (
                IsGerenteOrAdministrador().has_permission(
                    requisicao("get", user), None
                )
            ),
        }

    def test_existe_uma_implementacao_so(self):
        """O ponto do refactor: os call sites resolvem para a MESMA funcao.

        Concordar por coincidencia (duas copias com o mesmo texto) nao basta —
        foi assim que elas divergiram da primeira vez. Aqui a exigencia e
        identidade, entao reintroduzir uma copia local fica vermelho na hora.
        """
        self.assertIs(regra_do_viewsets, is_gerente_ou_admin)
        self.assertIs(papel_do_viewsets, get_user_group_name)
        self.assertIs(papel_das_views, get_user_group_name)
        self.assertFalse(hasattr(notifications, "_is_gerente_ou_admin"))

    def test_as_tres_copias_concordam(self):
        casos = [
            (self.superuser, True),
            (self.admin, True),
            (self.gerente, True),
            (self.responsavel, False),
            (self.sem_grupo, False),
            (AnonymousUser(), False),
        ]

        for user, esperado in casos:
            with self.subTest(usuario=str(user)):
                for nome, resultado in self.implementacoes(user).items():
                    self.assertEqual(
                        bool(resultado),
                        esperado,
                        f"{nome} divergiu para {user}",
                    )

    def test_superuser_sem_grupo_nenhum_ja_conta(self):
        """is_superuser sozinho basta: nao precisa estar no grupo Admin."""
        self.assertFalse(self.superuser.groups.exists())
        for nome, resultado in self.implementacoes(self.superuser).items():
            with self.subTest(implementacao=nome):
                self.assertTrue(resultado)

    def test_gerente_inativo_ainda_conta_como_gerente(self):
        """Nenhuma das tres olha is_active.

        Registrado porque e surpreendente: desativar um gerente NAO tira o
        acesso dele por esta regra (o login e que barra). Se a unificacao
        passar a checar is_active, este teste fica vermelho — e ai a mudanca
        precisa ser decidida, nao herdada sem querer.
        """
        inativo = usuario("inativo@email.com", "Gerente")
        inativo.is_active = False
        inativo.save(update_fields=["is_active"])

        for nome, resultado in self.implementacoes(inativo).items():
            with self.subTest(implementacao=nome):
                self.assertTrue(resultado)

    def test_usuario_none_devolve_false_em_vez_de_estourar(self):
        """Divergencia que existia entre as copias, resolvida pela mais segura.

        Antes: a copia de notifications devolvia False para None, a de
        viewsets estourava AttributeError. Nao e alcancavel pela API
        (request.user nunca e None), mas ha chamada fora de request — o digest
        das 7h — onde nao existe usuario garantido.

        A unificacao ficou com a versao COM guarda: None vira negativa, nao
        500. Adotar a outra teria quebrado o digest em producao sem quebrar
        nenhum teste de API.
        """
        self.assertFalse(is_gerente_ou_admin(None))
        self.assertFalse(regra_do_viewsets(None))


class PapelDoUsuarioTests(TestCase):
    """get_user_group_name: uma definicao, importada por views.py e viewsets.py."""

    def setUp(self):
        criar_grupos()
        self.superuser = User.objects.create_superuser(
            "root@email.com", password="123456"
        )
        self.admin = usuario("admin@email.com", "Admin")
        self.gerente = usuario("ger@email.com", "Gerente")
        self.responsavel = usuario("resp@email.com", "Responsavel")
        self.sem_grupo = usuario("nada@email.com")

        self.multi = usuario("multi@email.com", "Gerente")
        self.multi.groups.add(Group.objects.get(name="Responsavel"))

    def test_as_duas_copias_concordam(self):
        usuarios = [
            self.superuser, self.admin, self.gerente,
            self.responsavel, self.sem_grupo, self.multi,
        ]
        for user in usuarios:
            with self.subTest(usuario=str(user)):
                self.assertEqual(papel_das_views(user), papel_do_viewsets(user))

    def test_superuser_sem_grupo_e_admin(self):
        self.assertEqual(papel_do_viewsets(self.superuser), "Admin")

    def test_admin_ganha_de_qualquer_outro_grupo(self):
        self.admin.groups.add(Group.objects.get(name="Responsavel"))
        self.assertEqual(papel_do_viewsets(self.admin), "Admin")

    def test_sem_grupo_devolve_none(self):
        self.assertIsNone(papel_do_viewsets(self.sem_grupo))

    def test_usuario_em_dois_grupos_depende_da_ordem_do_banco(self):
        """groups.first() sem order_by: qual grupo volta e indefinido.

        Nao afirmamos QUAL — so que e um dos dois e que as duas copias
        respondem igual. Se a unificacao definir prioridade explicita
        (Admin > Gerente > Responsavel), este teste continua verde e vira o
        lugar certo para apertar a regra.
        """
        papel = papel_do_viewsets(self.multi)
        self.assertIn(papel, {"Gerente", "Responsavel"})
        self.assertEqual(papel, papel_das_views(self.multi))
