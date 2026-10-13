from datetime import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from app.models import Comprovante, Conta, ElementoDeDespesa, RegraDeCategoriaDoRecebedor
from app.services.plano_de_contas import garantir_plano_de_contas

from .fabricas import conta_padrao, criar_loja, lista, vincular_conta

OK, CONFERIR, DESCARTADO = "OK", "CONFERIR", "DESCARTADO"


def momento(dia):
    return timezone.make_aware(datetime(2026, 10, dia, 10, 0))


class BaseApi(TestCase):
    def setUp(self):
        for nome in ("Admin", "Gerente", "Funcionario"):
            Group.objects.get_or_create(name=nome)
        self.conta = conta_padrao()
        self.conta.modulo_comprovantes_ativo = True
        self.conta.save()
        self.loja = criar_loja(nome_loja="Loja A", cidade="Patos", endereco="Rua 1")
        self.user = User.objects.create_user(username="g@x.com", password="123456")
        self.user.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(self.user, self.conta)
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def criar(self, **campos):
        campos.setdefault("conta", self.conta)
        campos.setdefault("origem", "EMAIL")
        campos.setdefault("situacao", OK)
        campos.setdefault("valor", Decimal("10.00"))
        campos.setdefault("data_hora", momento(5))
        return Comprovante.objects.create(**campos)


class PermissaoTests(BaseApi):
    def test_modulo_desligado_e_403(self):
        self.conta.modulo_comprovantes_ativo = False
        self.conta.save()
        self.assertEqual(self.api.get("/api/v1/comprovantes/").status_code, 403)

    def test_funcionario_e_403(self):
        funcionario = User.objects.create_user(username="f@x.com", password="123456")
        funcionario.groups.add(Group.objects.get(name="Funcionario"))
        vincular_conta(funcionario, self.conta)
        self.api.force_authenticate(funcionario)
        self.assertEqual(self.api.get("/api/v1/comprovantes/").status_code, 403)

    def test_comprovante_de_outra_empresa_nao_aparece(self):
        outra = Conta.objects.create(nome="Outra", modulo_comprovantes_ativo=True)
        alheio = self.criar(conta=outra)
        self.assertEqual(lista(self.api.get("/api/v1/comprovantes/")), [])
        self.assertEqual(
            self.api.get(f"/api/v1/comprovantes/{alheio.public_id}/").status_code, 404
        )


class ListaETotaisTests(BaseApi):
    def test_totais_somam_so_ok_por_loja_e_sem_loja(self):
        self.criar(loja=self.loja, valor=Decimal("100.00"))
        self.criar(valor=Decimal("30.00"))
        self.criar(situacao=CONFERIR, valor=Decimal("999.00"))
        self.criar(situacao=DESCARTADO, valor=Decimal("888.00"))

        totais = self.api.get("/api/v1/comprovantes/").data["totais"]

        self.assertEqual(totais["valor"], "130.00")
        self.assertEqual(totais["conferir"], 1)
        por_loja = {item["nome"]: item["valor"] for item in totais["por_loja"]}
        self.assertEqual(por_loja, {"Loja A": "100.00", "Sem loja": "30.00"})

    def test_filtrar_situacao_nao_muda_os_totais(self):
        self.criar(valor=Decimal("100.00"))
        self.criar(situacao=CONFERIR)
        resposta = self.api.get("/api/v1/comprovantes/?situacao=CONFERIR")
        self.assertEqual(len(lista(resposta)), 1)
        self.assertEqual(resposta.data["totais"]["valor"], "100.00")

    def test_filtro_de_periodo_e_de_loja(self):
        self.criar(loja=self.loja, data_hora=momento(1))
        self.criar(loja=self.loja, data_hora=momento(20))
        self.criar(data_hora=momento(20))
        self.assertEqual(len(lista(self.api.get("/api/v1/comprovantes/?de=2026-10-10"))), 2)
        self.assertEqual(
            len(lista(self.api.get(f"/api/v1/comprovantes/?loja={self.loja.public_id}"))), 2
        )
        self.assertEqual(len(lista(self.api.get("/api/v1/comprovantes/?loja=sem"))), 1)

    def test_filtro_invalido_e_400(self):
        self.assertEqual(self.api.get("/api/v1/comprovantes/?de=ontem").status_code, 400)
        self.assertEqual(self.api.get("/api/v1/comprovantes/?loja=abc").status_code, 400)

    def test_sem_data_ainda_aparece_no_periodo(self):
        """Comprovante para conferir sem data lida nao pode sumir do filtro."""
        self.criar(situacao=CONFERIR, data_hora=None, valor=None)
        resposta = self.api.get("/api/v1/comprovantes/?de=2026-10-01&ate=2026-10-31")
        self.assertEqual(len(lista(resposta)), 1)


class EdicaoTests(BaseApi):
    def setUp(self):
        super().setUp()
        garantir_plano_de_contas(self.conta)
        self.aluguel = ElementoDeDespesa.objects.get(grupo__conta=self.conta, nome="Aluguel")

    def test_escolher_categoria_aprende_o_recebedor(self):
        comprovante = self.criar(recebedor_nome="Maria", recebedor_normalizado="MARIA")
        resposta = self.api.patch(
            f"/api/v1/comprovantes/{comprovante.public_id}/",
            {"elemento": str(self.aluguel.public_id)},
            format="json",
        )
        self.assertEqual(resposta.status_code, 200, resposta.data)
        self.assertEqual(resposta.data["elemento_nome"], "Aluguel")
        self.assertTrue(
            RegraDeCategoriaDoRecebedor.objects.filter(recebedor_normalizado="MARIA").exists()
        )

    def test_corrigir_marca_o_campo_e_renormaliza_o_recebedor(self):
        comprovante = self.criar()
        self.api.patch(
            f"/api/v1/comprovantes/{comprovante.public_id}/",
            {"valor": "12.50", "recebedor_nome": "Joãozinho  Gás"},
            format="json",
        )
        comprovante.refresh_from_db()
        self.assertEqual(comprovante.valor, Decimal("12.50"))
        self.assertEqual(comprovante.recebedor_normalizado, "JOAOZINHO GAS")
        self.assertEqual(sorted(comprovante.campos_editados), ["recebedor_nome", "valor"])

    def test_loja_e_categoria_de_outra_empresa_sao_recusadas(self):
        outra = Conta.objects.create(nome="Outra")
        loja_alheia = criar_loja(conta=outra, nome_loja="X", cidade="X", endereco="X")
        garantir_plano_de_contas(outra)
        elemento_alheio = ElementoDeDespesa.objects.filter(grupo__conta=outra).first()
        comprovante = self.criar()
        url = f"/api/v1/comprovantes/{comprovante.public_id}/"
        self.assertEqual(
            self.api.patch(url, {"loja": str(loja_alheia.public_id)}, format="json").status_code,
            400,
        )
        self.assertEqual(
            self.api.patch(
                url, {"elemento": str(elemento_alheio.public_id)}, format="json"
            ).status_code,
            400,
        )

    def test_tirar_a_loja(self):
        comprovante = self.criar(loja=self.loja)
        self.api.patch(
            f"/api/v1/comprovantes/{comprovante.public_id}/", {"loja": None}, format="json"
        )
        comprovante.refresh_from_db()
        self.assertIsNone(comprovante.loja)


class AcoesTests(BaseApi):
    def test_confirmar_passa_para_ok(self):
        comprovante = self.criar(situacao=CONFERIR, motivo="Parece Pix recebido")
        resposta = self.api.post(f"/api/v1/comprovantes/{comprovante.public_id}/confirmar/")
        self.assertEqual(resposta.status_code, 200)
        comprovante.refresh_from_db()
        self.assertEqual((comprovante.situacao, comprovante.motivo), (OK, ""))

    def test_confirmar_sem_valor_e_400(self):
        comprovante = self.criar(situacao=CONFERIR, valor=None)
        resposta = self.api.post(f"/api/v1/comprovantes/{comprovante.public_id}/confirmar/")
        self.assertEqual(resposta.status_code, 400)

    def test_descartar(self):
        comprovante = self.criar()
        self.api.post(f"/api/v1/comprovantes/{comprovante.public_id}/descartar/")
        comprovante.refresh_from_db()
        self.assertEqual(comprovante.situacao, DESCARTADO)

    def test_arquivo_volta_com_o_tipo(self):
        comprovante = self.criar(arquivo=b"%PDF-1.4", arquivo_tipo="application/pdf")
        resposta = self.api.get(f"/api/v1/comprovantes/{comprovante.public_id}/arquivo/")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Content-Type"], "application/pdf")
        self.assertEqual(resposta.content, b"%PDF-1.4")

    def test_arquivo_pode_abrir_dentro_da_tela(self):
        # O PDF aparece num iframe do detalhe; com o DENY padrao o navegador bloqueia.
        comprovante = self.criar(arquivo=b"%PDF-1.4", arquivo_tipo="application/pdf")
        resposta = self.api.get(f"/api/v1/comprovantes/{comprovante.public_id}/arquivo/")
        self.assertEqual(resposta["X-Frame-Options"], "SAMEORIGIN")

    def test_sem_arquivo_e_404(self):
        comprovante = self.criar()
        resposta = self.api.get(f"/api/v1/comprovantes/{comprovante.public_id}/arquivo/")
        self.assertEqual(resposta.status_code, 404)
