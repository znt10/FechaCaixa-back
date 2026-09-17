"""Os cargos existem, e a lista deles vive num lugar so.

Isto ja foi uma fixture JSON, e ela custou caro: distribuia permissoes de
"produto" e "estoque" meses depois desses modelos terem sido apagados, e num
banco novo o loaddata estourava — derrubando a criacao dos quatro grupos e
deixando todo login em 403 "Usuario sem grupo".

Virou comando por dois motivos. O primeiro e que um comando le
`app/grupos.py`, entao a lista de cargos existe uma vez so; com o JSON eram
duas listas que ninguem obrigava a concordar. O segundo e que um comando nao
tem como quebrar por causa de um modelo que sumiu.
"""

from django.apps import apps
from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase

from app.grupos import (
    GRUPO_ADMIN,
    GRUPO_FUNCIONARIO,
    GRUPO_GERENTE,
    TODOS_OS_GRUPOS,
)
from app.permissions import is_admin, is_funcionario, is_gerente


class GarantirGruposTests(TestCase):
    def setUp(self):
        Group.objects.all().delete()
        call_command("garantir_grupos", verbosity=0)

    def test_cria_os_quatro_cargos(self):
        self.assertEqual(
            sorted(Group.objects.values_list("name", flat=True)),
            sorted(TODOS_OS_GRUPOS),
        )

    def test_o_funcionario_existe_num_banco_novo(self):
        """Ele nao estava na fixture: so nascia quando alguem cadastrava o
        primeiro funcionario, entao num banco recem-criado nao existia."""
        self.assertTrue(Group.objects.filter(name=GRUPO_FUNCIONARIO).exists())

    def test_rodar_duas_vezes_nao_duplica(self):
        """Roda em todo boot: precisa ser idempotente por construcao."""
        call_command("garantir_grupos", verbosity=0)

        self.assertEqual(Group.objects.count(), len(TODOS_OS_GRUPOS))

    def test_recria_um_cargo_apagado_a_mao(self):
        """Alguem apaga um grupo pelo /admin/ e o proximo boot conserta.

        Uma migracao de dados nao faria isso: ela roda uma vez e pronto.
        """
        Group.objects.filter(name=GRUPO_FUNCIONARIO).delete()

        call_command("garantir_grupos", verbosity=0)

        self.assertTrue(Group.objects.filter(name=GRUPO_FUNCIONARIO).exists())

    def test_limpa_permissao_de_modelo_apagado(self):
        """O Django nao apaga content type sozinho quando a migracao roda sem
        interacao — que e sempre, num deploy. Sobravam 32 aqui."""
        fantasma = ContentType.objects.create(app_label="app", model="produto")
        Permission.objects.create(
            codename="add_produto", name="Can add produto", content_type=fantasma
        )

        call_command("garantir_grupos", verbosity=0)

        self.assertFalse(
            ContentType.objects.filter(app_label="app", model="produto").exists()
        )

    def test_nao_distribui_permissao_de_modelo_que_nao_existe(self):
        """O defeito que passou despercebido, agora com alarme."""
        vivos = {
            modelo._meta.model_name
            for modelo in apps.get_app_config("app").get_models()
        }
        for grupo in Group.objects.all():
            mortas = [
                p.codename
                for p in grupo.permissions.all()
                if p.content_type.app_label == "app"
                and p.content_type.model not in vivos
            ]
            self.assertEqual(mortas, [], f"{grupo.name} distribui {mortas}")


class OsNomesQueAAutorizacaoLeTests(TestCase):
    """Cada helper le o grupo pelo nome. Um acento a mais em qualquer um deles
    devolveria False sem erro nenhum — a pessoa entra e nao enxerga nada."""

    def setUp(self):
        call_command("garantir_grupos", verbosity=0)

    def _com_grupo(self, nome):
        user = User.objects.create_user(username=f"{nome}@x.com", password="x")
        user.groups.add(Group.objects.get(name=nome))
        return user

    def test_cada_cargo_e_reconhecido_pelo_seu_helper(self):
        casos = [
            (GRUPO_ADMIN, is_admin),
            (GRUPO_GERENTE, is_gerente),
            (GRUPO_FUNCIONARIO, is_funcionario),
        ]
        for nome, helper in casos:
            with self.subTest(cargo=nome):
                self.assertTrue(helper(self._com_grupo(nome)))

    def test_um_cargo_nao_e_confundido_com_outro(self):
        funcionario = self._com_grupo(GRUPO_FUNCIONARIO)

        self.assertTrue(is_funcionario(funcionario))
        self.assertFalse(is_gerente(funcionario))
        self.assertFalse(is_admin(funcionario))


class EnsureAdminTests(TestCase):
    def test_o_admin_recebe_as_permissoes_por_consulta(self):
        """Por consulta, e nao por lista fixa: lista envelhece calada."""
        call_command("ensure_admin", verbosity=0)

        admin = Group.objects.get(name=GRUPO_ADMIN)
        self.assertEqual(
            admin.permissions.count(),
            Permission.objects.filter(content_type__app_label="app").count(),
        )
