from django.contrib.auth.models import Group, User
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from rest_framework.test import APIClient, APITestCase

from app.models import (
    CategoriaDeSalgado,
    Conta,
    Desperdicio,
    FechamentoCaixa,
    Salgado,
)
from app.services.catalogo_de_salgados import garantir_catalogo_de_salgados

from .fabricas import DIA_COMUM, criar_loja, vincular_conta


class CatalogoTests(TestCase):
    def setUp(self):
        self.conta = Conta.objects.create(nome="Marina")
        self.grande = CategoriaDeSalgado.objects.create(
            conta=self.conta, nome="Salgados grande", ordem=0
        )
        self.mini = CategoriaDeSalgado.objects.create(
            conta=self.conta, nome="Salgados mini", ordem=1
        )

    def test_mesmo_nome_em_categorias_diferentes_e_aceito(self):
        """Coxinha existe em grande E em mini: e o caso real do catalogo."""
        Salgado.objects.create(categoria=self.grande, nome="Coxinha")
        Salgado.objects.create(categoria=self.mini, nome="Coxinha")

        self.assertEqual(Salgado.objects.filter(nome="Coxinha").count(), 2)

    def test_nome_repetido_na_mesma_categoria_e_recusado(self):
        Salgado.objects.create(categoria=self.grande, nome="Kibe")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Salgado.objects.create(categoria=self.grande, nome="Kibe")

    def test_categoria_repetida_na_mesma_conta_e_recusada(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CategoriaDeSalgado.objects.create(
                    conta=self.conta, nome="Salgados grande"
                )

    def test_duas_contas_podem_ter_a_mesma_categoria(self):
        outra = Conta.objects.create(nome="Primavera")
        CategoriaDeSalgado.objects.create(conta=outra, nome="Salgados grande")

        self.assertEqual(
            CategoriaDeSalgado.objects.filter(nome="Salgados grande").count(), 2
        )

    def test_conta_do_salgado_vem_da_categoria(self):
        coxinha = Salgado.objects.create(categoria=self.grande, nome="Coxinha")

        self.assertEqual(coxinha.conta, self.conta)


class DesperdicioNoBancoTests(TestCase):
    def setUp(self):
        self.conta = Conta.objects.create(nome="Marina")
        self.loja = criar_loja(
            nome_loja="Centro", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        categoria = CategoriaDeSalgado.objects.create(
            conta=self.conta, nome="Salgados grande"
        )
        self.coxinha = Salgado.objects.create(categoria=categoria, nome="Coxinha")
        self.fechamento = FechamentoCaixa.objects.create(
            loja=self.loja, nome_funcionario="Ana", data=DIA_COMUM, periodo="TARDE"
        )

    def test_apagar_salgado_com_historico_e_impedido(self):
        """PROTECT: apagar o item apagaria o desperdicio lancado junto."""
        Desperdicio.objects.create(
            fechamento=self.fechamento, salgado=self.coxinha, quantidade=8
        )

        with self.assertRaises(ProtectedError):
            self.coxinha.delete()

    def test_apagar_o_fechamento_leva_as_linhas(self):
        Desperdicio.objects.create(
            fechamento=self.fechamento, salgado=self.coxinha, quantidade=8
        )

        self.fechamento.delete()

        self.assertEqual(Desperdicio.objects.count(), 0)


class SemeaduraTests(TestCase):
    def setUp(self):
        self.conta = Conta.objects.create(nome="Marina")

    def test_semeia_seis_categorias_e_quarenta_e_dois_itens(self):
        garantir_catalogo_de_salgados(self.conta)

        self.assertEqual(
            CategoriaDeSalgado.objects.filter(conta=self.conta).count(), 6
        )
        self.assertEqual(
            Salgado.objects.filter(categoria__conta=self.conta).count(), 42
        )

    def test_a_ordem_poe_grande_antes_de_mini(self):
        garantir_catalogo_de_salgados(self.conta)

        nomes = list(
            CategoriaDeSalgado.objects.filter(conta=self.conta).values_list(
                "nome", flat=True
            )
        )

        self.assertEqual(nomes[0], "Salgados grande")
        self.assertEqual(nomes[1], "Salgados mini")

    def test_coxinha_nasce_nas_duas_familias(self):
        garantir_catalogo_de_salgados(self.conta)

        self.assertEqual(
            Salgado.objects.filter(
                categoria__conta=self.conta, nome="Coxinha"
            ).count(),
            2,
        )

    def test_semear_duas_vezes_nao_duplica(self):
        garantir_catalogo_de_salgados(self.conta)
        garantir_catalogo_de_salgados(self.conta)

        self.assertEqual(
            Salgado.objects.filter(categoria__conta=self.conta).count(), 42
        )

    def test_nao_repoe_o_que_a_empresa_apagou(self):
        """Sai cedo se ja ha qualquer categoria: semear de novo reintroduziria
        o que a empresa apagou de proposito."""
        garantir_catalogo_de_salgados(self.conta)
        Salgado.objects.filter(
            categoria__conta=self.conta, nome="Salsicha"
        ).delete()

        garantir_catalogo_de_salgados(self.conta)

        self.assertFalse(
            Salgado.objects.filter(
                categoria__conta=self.conta, nome="Salsicha"
            ).exists()
        )

    def test_cada_conta_tem_o_seu(self):
        outra = Conta.objects.create(nome="Primavera")

        garantir_catalogo_de_salgados(self.conta)
        garantir_catalogo_de_salgados(outra)

        self.assertEqual(CategoriaDeSalgado.objects.count(), 12)


class CatalogoNaAPITests(APITestCase):
    def setUp(self):
        self.conta = Conta.objects.create(nome="Marina")
        self.outra = Conta.objects.create(nome="Primavera")
        self.gerente = User.objects.create_user(
            username="gerente@marina.com", password="x"
        )
        Group.objects.get_or_create(name="Gerente")[0].user_set.add(self.gerente)
        vincular_conta(self.gerente, self.conta)
        self.client = APIClient()

    def test_listar_semeia_o_catalogo(self):
        """Quem abre a tela antes de cadastrar nada nao pode ver lista vazia."""
        self.client.force_authenticate(self.gerente)

        resp = self.client.get("/api/v1/categorias-de-salgado/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 6)

    def test_a_lista_de_salgados_nao_pagina(self):
        """42 itens contra um PAGE_SIZE de 50 — e a lista cresce. Paginar aqui
        repetiria o bug dos encarregados, onde a pagina 2 nao existia na tela."""
        self.client.force_authenticate(self.gerente)

        resp = self.client.get("/api/v1/salgados/")

        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.data, list)
        self.assertEqual(len(resp.data), 42)

    def test_nao_ve_o_catalogo_da_outra_empresa(self):
        garantir_catalogo_de_salgados(self.outra)
        self.client.force_authenticate(self.gerente)

        resp = self.client.get("/api/v1/salgados/")

        nomes_de_conta = {
            Salgado.objects.get(public_id=item["id"]).categoria.conta_id
            for item in resp.data
        }
        self.assertEqual(nomes_de_conta, {self.conta.id})

    def test_categoria_repetida_devolve_400_e_nao_500(self):
        self.client.force_authenticate(self.gerente)
        self.client.get("/api/v1/categorias-de-salgado/")  # semeia

        resp = self.client.post(
            "/api/v1/categorias-de-salgado/",
            {"nome": "Salgados grande"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_mesmo_nome_em_categorias_diferentes_passa_pela_api(self):
        self.client.force_authenticate(self.gerente)
        self.client.get("/api/v1/categorias-de-salgado/")
        mini = CategoriaDeSalgado.objects.get(
            conta=self.conta, nome="Fogazzas mini"
        )

        resp = self.client.post(
            "/api/v1/salgados/",
            {"nome": "Coxinha", "categoria": str(mini.public_id)},
            format="json",
        )

        self.assertEqual(resp.status_code, 201)

    def test_categoria_de_outra_conta_no_cadastro_de_salgado_devolve_400(self):
        """A defesa mais importante do diff: sem ela, um item nasceria
        pendurado na categoria de outra empresa so por enviar o public_id
        certo no corpo do pedido."""
        garantir_catalogo_de_salgados(self.outra)
        categoria_de_fora = CategoriaDeSalgado.objects.filter(
            conta=self.outra
        ).first()
        self.client.force_authenticate(self.gerente)

        resp = self.client.post(
            "/api/v1/salgados/",
            {"nome": "Bolinho novo", "categoria": str(categoria_de_fora.public_id)},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_mover_para_categoria_com_nome_igual_devolve_400_e_nao_500(self):
        """PATCH parcial que so troca a categoria: o nome nao vem no corpo,
        e e exatamente esse caminho que colide com a UniqueConstraint do
        banco se o serializer nao resolver o nome atual do item por
        fallback. "Coxinha" ja existe em Salgados grande E Salgados mini no
        catalogo semeado — mover uma para a categoria da outra tem que
        recusar com 400, nunca subir o IntegrityError como 500."""
        self.client.force_authenticate(self.gerente)
        self.client.get("/api/v1/categorias-de-salgado/")  # semeia
        grande = CategoriaDeSalgado.objects.get(
            conta=self.conta, nome="Salgados grande"
        )
        mini = CategoriaDeSalgado.objects.get(conta=self.conta, nome="Salgados mini")
        coxinha_do_grande = Salgado.objects.get(categoria=grande, nome="Coxinha")

        resp = self.client.patch(
            f"/api/v1/salgados/{coxinha_do_grande.public_id}/",
            {"categoria": str(mini.public_id)},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_mover_para_categoria_sem_colisao_e_aceito(self):
        """Caminho feliz do mesmo PATCH parcial: "Salsicha" so existe em
        Salgados grande no catalogo semeado, entao move-la para Salgados
        mini nao colide com nada. Sem este teste, um fallback com a logica
        invertida (que recusasse sempre) passaria despercebido."""
        self.client.force_authenticate(self.gerente)
        self.client.get("/api/v1/categorias-de-salgado/")  # semeia
        grande = CategoriaDeSalgado.objects.get(
            conta=self.conta, nome="Salgados grande"
        )
        mini = CategoriaDeSalgado.objects.get(conta=self.conta, nome="Salgados mini")
        salsicha = Salgado.objects.get(categoria=grande, nome="Salsicha")

        resp = self.client.patch(
            f"/api/v1/salgados/{salsicha.public_id}/",
            {"categoria": str(mini.public_id)},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        salsicha.refresh_from_db()
        self.assertEqual(salsicha.categoria_id, mini.id)

    def test_categoria_de_outra_conta_nao_aparece_no_detalhe(self):
        garantir_catalogo_de_salgados(self.outra)
        categoria_de_fora = CategoriaDeSalgado.objects.filter(
            conta=self.outra
        ).first()
        self.client.force_authenticate(self.gerente)
        url = f"/api/v1/categorias-de-salgado/{categoria_de_fora.public_id}/"

        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.patch(url, {"nome": "Roubada"}, format="json").status_code,
            404,
        )
        self.assertEqual(self.client.delete(url).status_code, 404)

    def test_salgado_de_outra_conta_nao_aparece_no_detalhe(self):
        garantir_catalogo_de_salgados(self.outra)
        salgado_de_fora = Salgado.objects.filter(
            categoria__conta=self.outra
        ).first()
        self.client.force_authenticate(self.gerente)
        url = f"/api/v1/salgados/{salgado_de_fora.public_id}/"

        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.patch(url, {"nome": "Roubado"}, format="json").status_code,
            404,
        )
        self.assertEqual(self.client.delete(url).status_code, 404)


class CatalogoNoFormularioTests(APITestCase):
    """O formulario da loja nao tem login: quem autentica e o aparelho.

    Sem esta ponta a loja tomaria 401 e o bloco de desperdicio listaria zero
    itens — e o mesmo desenho de duas pontas do EncarregadoViewSet.
    """

    def setUp(self):
        self.conta = Conta.objects.create(nome="Marina")
        garantir_catalogo_de_salgados(self.conta)
        self.client = APIClient()

    def entrar(self):
        self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": self.conta.codigo_acesso, "apelido": "Caixa"},
            format="json",
        )

    def test_aparelho_da_loja_lista_os_salgados(self):
        self.entrar()

        resp = self.client.get("/api/v1/salgados/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 42)

    def test_sem_entrar_no_formulario_nao_lista(self):
        resp = self.client.get("/api/v1/salgados/")

        self.assertIn(resp.status_code, (401, 403))

    def test_aparelho_nao_cria_salgado(self):
        """Ler para o seletor, sim; mexer no cadastro, nao."""
        self.entrar()
        categoria = CategoriaDeSalgado.objects.filter(conta=self.conta).first()

        resp = self.client.post(
            "/api/v1/salgados/",
            {"nome": "Bolinho novo", "categoria": str(categoria.public_id)},
            format="json",
        )

        self.assertIn(resp.status_code, (401, 403))
