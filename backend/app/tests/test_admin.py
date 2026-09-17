"""A Loja nao tem mais os campos que vieram do Unistock.

Este arquivo testava o LojaAdmin, que restringia o seletor de "gerente" ao
grupo Gerente. O campo saiu na migracao 0037, junto com responsavel, email,
telefone_whatsapp e tipo — a loja nao tem login proprio, quem lanca o caixa
entra pelo codigo da empresa no aparelho, e a gerente administra as lojas da
empresa dela, e nao um subconjunto no nome dela.

O que sobrou e a guarda contra a volta deles: sao cinco campos que o
/admin exibe automaticamente, entao readicionar qualquer um faz o formulario
de cadastro de loja voltar a pedir dado que ninguem preenche.
"""

from django.test import TestCase

from app.models import Loja


class LojaSemCamposDoUnistockTests(TestCase):
    CAMPOS_REMOVIDOS = ("gerente", "responsavel", "email", "telefone_whatsapp", "tipo")

    def test_os_campos_do_unistock_nao_voltaram(self):
        campos = {f.name for f in Loja._meta.get_fields()}

        for campo in self.CAMPOS_REMOVIDOS:
            with self.subTest(campo=campo):
                self.assertNotIn(campo, campos)

    def test_a_loja_tem_so_o_que_o_fechacaixa_usa(self):
        """O cadastro e conta + identificacao + endereco. Nada de pessoas."""
        campos = {f.name for f in Loja._meta.get_fields() if not f.auto_created}

        self.assertEqual(
            campos,
            {
                "public_id",
                "created_at",
                "updated_at",
                "is_deleted",
                "conta",
                "nome_loja",
                "cidade",
                "endereco",
                "ativo",
                "cnpj",
            },
        )


class AdminDizDeQualEmpresaTests(TestCase):
    """No /admin da com duas empresas, cada linha tem que dizer de quem e.

    Com uma empresa so ninguem sentia falta. Com duas, a lista de lojas mostrava
    "centro, Pirituba, Clipper" e nada mais — nao dava para saber qual era de
    qual empresa, e a de fechamentos so filtrava loja a loja, sem um "me mostra
    esta empresa inteira".

    O teste passa pelo changelist renderizado, e nao pelas opcoes do ModelAdmin:
    o que se quer garantir e que a informacao chega na tela, nao que uma tupla
    tem certo conteudo.
    """

    def setUp(self):
        from django.contrib.auth.models import User

        from app.models import Conta, FechamentoCaixa

        from .fabricas import criar_loja

        self.primavera = Conta.objects.create(nome="Primavera")
        self.outra = Conta.objects.create(nome="Aurora Salgados")

        self.loja_da_primavera = criar_loja(
            nome_loja="Pirituba", cidade="Sao Paulo", endereco="Rua 1",
            conta=self.primavera,
        )
        self.loja_da_outra = criar_loja(
            nome_loja="Clipper", cidade="Patos", endereco="Rua 2", conta=self.outra,
        )

        for loja in (self.loja_da_primavera, self.loja_da_outra):
            FechamentoCaixa.objects.create(
                loja=loja, nome_funcionario=f"Quem lancou na {loja.nome_loja}",
                data="2026-08-26", periodo="MANHA", pix="10.00",
            )

        dono = User.objects.create_superuser(
            username="dono@fechacaixa.local", email="dono@fechacaixa.local",
            password="uma-senha-de-teste",
        )
        self.client.force_login(dono)

    def test_a_lista_de_lojas_tem_a_coluna_da_empresa(self):
        resposta = self.client.get("/admin/app/loja/")

        # A classe da celula, e nao so o nome da empresa: o nome tambem apareceria
        # pela barra de filtros, e ai o teste passaria sem a coluna existir.
        self.assertContains(resposta, "field-conta")
        self.assertContains(resposta, "Primavera")

    def test_a_lista_de_lojas_filtra_por_empresa(self):
        resposta = self.client.get(
            f"/admin/app/loja/?conta__id__exact={self.primavera.id}"
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Pirituba")
        self.assertNotContains(resposta, "Clipper")

    def test_a_lista_de_fechamentos_tem_a_coluna_da_empresa(self):
        resposta = self.client.get("/admin/app/fechamentocaixa/")

        self.assertContains(resposta, "field-empresa")
        self.assertContains(resposta, "Primavera")

    def test_a_lista_de_fechamentos_filtra_pela_empresa_da_loja(self):
        """O fechamento nao guarda conta de proposito — ela vem da loja. O filtro
        atravessa a relacao em vez de duplicar o campo, que poderia divergir."""
        resposta = self.client.get(
            f"/admin/app/fechamentocaixa/?loja__conta__id__exact={self.primavera.id}"
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Quem lancou na Pirituba")
        self.assertNotContains(resposta, "Quem lancou na Clipper")


class NotaFiscalNoAdminTests(TestCase):
    """A nota e prova, e o /admin e o ultimo caminho que ainda a reescrevia.

    A API ja prende os campos que vem do XML. O /admin nao prendia: o
    formulario trazia numero, serie, data de emissao e valor como campos de
    digitacao, e conta e loja como seletores — ou seja, o relatorio podia
    passar a discordar do XML guardado, em silencio, que e exatamente o que
    este modulo existe para impedir. Que hoje so o dono da plataforma tenha
    is_staff nao muda nada: a prova nao pode depender de quem tem a senha.

    O teste mede pelo formulario que o ModelAdmin monta, e nao pela tupla
    readonly_fields: e o formulario que decide o que a tela deixa gravar.
    """

    CAMPOS_QUE_NAO_SE_DIGITAM = (
        "chave", "xml_bruto", "numero", "serie", "data_emissao",
        "valor_total", "conta", "loja",
    )

    def test_o_formulario_nao_deixa_reescrever_a_prova(self):
        from django.contrib import admin as django_admin
        from django.contrib.auth.models import User
        from django.test import RequestFactory

        from app.models import NotaFiscal

        pedido = RequestFactory().get("/admin/app/notafiscal/")
        pedido.user = User.objects.create_superuser(
            username="dono@fechacaixa.local", password="uma-senha-de-teste",
        )
        model_admin = django_admin.site._registry[NotaFiscal]

        gravaveis = model_admin.get_form(pedido).base_fields.keys()

        for campo in self.CAMPOS_QUE_NAO_SE_DIGITAM:
            with self.subTest(campo=campo):
                self.assertNotIn(campo, gravaveis)
