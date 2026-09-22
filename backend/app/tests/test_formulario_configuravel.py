"""O formulario sob medida para cada empresa.

O FechaCaixa nasceu numa rede de salgados, e o formulario perguntava de
salgado para todo mundo. Cada empresa agora diz quais perguntas opcionais o
turno responde, se usa catalogo de itens e como chama o que vende. Tudo nasce
ligado e com "salgados": quem ja usa o sistema nao ve diferenca nenhuma no dia
do deploy.

Quem muda e a gerencia, pela tela Empresa, e o dono da plataforma, pelo
/admin/. Quem lanca o caixa, nao.
"""

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APITestCase

from app.models import Conta

from .fabricas import vincular_conta

PERGUNTAS = (
    "pergunta_retirada",
    "pergunta_despesa",
    "pergunta_devolucao",
    "pergunta_consumo",
)
CONFIGURACAO = PERGUNTAS + ("catalogo_ativo", "nome_dos_itens")


class ContaNasceComoOFormularioDeHojeTests(TestCase):
    def test_todas_as_perguntas_nascem_ligadas(self):
        conta = Conta.objects.create(nome="Aurora Salgados")

        for campo in PERGUNTAS:
            self.assertIs(getattr(conta, campo), True, campo)

    def test_o_catalogo_nasce_ligado_e_chamando_de_salgados(self):
        conta = Conta.objects.create(nome="Aurora Salgados")

        self.assertIs(conta.catalogo_ativo, True)
        self.assertEqual(conta.nome_dos_itens, "salgados")


class ConfiguracaoChegaAoFormularioTests(APITestCase):
    """O formulario da loja nao tem login: e a rota publica de acesso que
    precisa contar quais perguntas mostrar."""

    def setUp(self):
        cache.clear()
        self.conta = Conta.objects.create(
            nome="Padaria Central",
            pergunta_consumo=False,
            catalogo_ativo=False,
            nome_dos_itens="pães",
        )

    def entrar(self):
        return self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": self.conta.codigo_acesso},
            format="json",
        )

    def test_o_acesso_devolve_a_configuracao(self):
        empresa = self.entrar().data["empresa"]

        self.assertIs(empresa["pergunta_consumo"], False)
        self.assertIs(empresa["pergunta_retirada"], True)
        self.assertIs(empresa["catalogo_ativo"], False)
        self.assertEqual(empresa["nome_dos_itens"], "pães")

    def test_a_empresa_do_formulario_devolve_a_configuracao(self):
        """E a rota que o aparelho ja conectado consulta ao abrir o formulario:
        sem os campos aqui, a pergunta desligada so sumiria no proximo acesso
        com codigo."""
        self.entrar()

        empresa = self.client.get("/api/v1/formulario/empresa/").data

        for campo in CONFIGURACAO:
            self.assertIn(campo, empresa)
        self.assertIs(empresa["pergunta_consumo"], False)


class GerenciaConfiguraOFormularioTests(APITestCase):
    URL = "/api/v1/minha-empresa/"

    def setUp(self):
        cache.clear()
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        gerente = User.objects.create_user(username="g@aurora.com", password="x")
        gerente.groups.add(Group.objects.get_or_create(name="Gerente")[0])
        vincular_conta(gerente, self.conta)
        self.client.force_authenticate(user=gerente)

    def test_a_tela_empresa_mostra_a_configuracao(self):
        resp = self.client.get(self.URL)

        for campo in CONFIGURACAO:
            self.assertIn(campo, resp.data)

    def test_desliga_uma_pergunta(self):
        resp = self.client.patch(self.URL, {"pergunta_consumo": False}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.conta.refresh_from_db()
        self.assertIs(self.conta.pergunta_consumo, False)
        # So a que foi pedida: um PATCH de uma pergunta nao pode religar nem
        # desligar as outras.
        self.assertIs(self.conta.pergunta_retirada, True)

    def test_desliga_o_catalogo(self):
        self.client.patch(self.URL, {"catalogo_ativo": False}, format="json")

        self.conta.refresh_from_db()
        self.assertIs(self.conta.catalogo_ativo, False)

    def test_troca_o_nome_dos_itens(self):
        resp = self.client.patch(self.URL, {"nome_dos_itens": "  pães "}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.conta.refresh_from_db()
        # Sem os espacos: o nome entra no meio de frase ("Houve perda de
        # pães?"), e espaco colado vira buraco no texto.
        self.assertEqual(self.conta.nome_dos_itens, "pães")

    def test_nome_em_branco_e_recusado_com_frase_da_tela(self):
        """Nome vazio deixaria a pergunta "Houve perda de ?" no formulario."""
        resp = self.client.patch(self.URL, {"nome_dos_itens": "   "}, format="json")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            str(resp.data["nome_dos_itens"][0]),
            "Diga como a empresa chama o que vende. Ex.: salgados, pães.",
        )
        self.conta.refresh_from_db()
        self.assertEqual(self.conta.nome_dos_itens, "salgados")

    def test_quem_lanca_o_caixa_nao_muda_o_formulario(self):
        responsavel = User.objects.create_user(username="loja@aurora.com", password="x")
        responsavel.groups.add(Group.objects.get_or_create(name="Responsavel")[0])
        vincular_conta(responsavel, self.conta)
        self.client.force_authenticate(user=responsavel)

        resp = self.client.patch(self.URL, {"pergunta_consumo": False}, format="json")

        self.assertEqual(resp.status_code, 403)
        self.conta.refresh_from_db()
        self.assertIs(self.conta.pergunta_consumo, True)


class DonoDaPlataformaConfiguraPeloAdminTests(TestCase):
    def setUp(self):
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        dono = User.objects.create_superuser(
            username="dono@fechacaixa.local", email="dono@fechacaixa.local",
            password="uma-senha-de-teste",
        )
        self.client.force_login(dono)

    def test_a_tela_da_conta_tem_os_campos_do_formulario(self):
        resp = self.client.get(f"/admin/app/conta/{self.conta.pk}/change/")

        self.assertEqual(resp.status_code, 200)
        for campo in CONFIGURACAO:
            self.assertContains(resp, f'name="{campo}"')


class CatalogoNoUsuarioLogadoTests(APITestCase):
    """O painel decide se desenha a aba Catalogo e o filtro Desperdicio pelo
    `/user/me/`, e nao pela tela Empresa: Saidas tambem e do Funcionario, e a
    tela Empresa responde 403 para ele — o filtro nunca sumiria."""

    URL = "/api/v1/user/me/"

    def setUp(self):
        self.conta = Conta.objects.create(nome="Padaria Central")
        self.funcionario = User.objects.create_user(username="f@padaria.com", password="x")
        self.funcionario.groups.add(Group.objects.get_or_create(name="Funcionario")[0])
        vincular_conta(self.funcionario, self.conta)
        self.client.force_authenticate(user=self.funcionario)

    def test_diz_que_o_catalogo_esta_ligado(self):
        resp = self.client.get(self.URL)

        self.assertIs(resp.data["modulos"]["catalogo"], True)

    def test_diz_que_o_catalogo_esta_desligado(self):
        self.conta.catalogo_ativo = False
        self.conta.save()

        resp = self.client.get(self.URL)

        self.assertIs(resp.data["modulos"]["catalogo"], False)

    def test_o_dono_da_plataforma_ve_o_catalogo_mesmo_sem_empresa(self):
        """Mesma regra das notas: sem empresa, o superuser confere tudo de
        fora, e a aba nao pode sumir justamente para ele."""
        dono = User.objects.create_superuser(username="dono@x.com", password="x")
        self.client.force_authenticate(user=dono)

        resp = self.client.get(self.URL)

        self.assertIs(resp.data["modulos"]["catalogo"], True)
