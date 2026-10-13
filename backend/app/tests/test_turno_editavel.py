"""A gerencia corrige o turno de um lancamento mandado no turno errado.

Acontece: quem fecha a tarde escolhe "Manha" no formulario, e o painel passa a
mostrar duas manhas e nenhuma tarde. Ate aqui o unico conserto era cancelar e
lancar de novo, jogando fora o caixa correto junto. Agora o turno se corrige
pelo mesmo PATCH dos valores — mas so para um turno que existe naquele dia:
num dia comum manha ou tarde, no domingo o domingo, no feriado o do dia
inteiro.
"""

from django.contrib.auth.models import Group, User
from rest_framework.test import APIClient, APITestCase

from app.models import Conta, FechamentoCaixa, Feriado

from .fabricas import DIA_COMUM, criar_loja, vincular_conta

UM_DOMINGO = "2026-08-09"
SETE_DE_SETEMBRO = "2026-09-07"  # segunda-feira, feriado nacional


class TurnoEditavelTests(APITestCase):
    def setUp(self):
        self.conta = Conta.objects.create(nome="Marina")
        self.loja = criar_loja(
            nome_loja="Loja Centro", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.gerente = User.objects.create_user(
            username="gerente", email="gerente@x.com", password="x"
        )
        self.gerente.groups.add(Group.objects.get_or_create(name="Gerente")[0])
        vincular_conta(self.gerente, self.conta)
        self.client = APIClient()
        self.client.force_authenticate(self.gerente)

    def lancado(self, data=DIA_COMUM, periodo="MANHA", loja=None):
        return FechamentoCaixa.objects.create(
            loja=loja or self.loja, nome_funcionario="Ana", data=data,
            periodo=periodo, pix="100.00", cartao="0.00", dinheiro="0.00",
            link_pagamento="0.00",
        )

    def corrigir(self, fechamento, **campos):
        return self.client.patch(
            f"/api/v1/fechamentos-caixa/{fechamento.public_id}/", campos, format="json"
        )

    def test_a_manha_mandada_errado_vira_tarde(self):
        fechamento = self.lancado(periodo="MANHA")

        resp = self.corrigir(fechamento, periodo="TARDE")

        self.assertEqual(resp.status_code, 200, resp.data)
        fechamento.refresh_from_db()
        self.assertEqual(fechamento.periodo, "TARDE")
        self.assertEqual(resp.data["periodo"], "TARDE")

    def test_a_troca_de_turno_fica_registrada_como_correcao(self):
        """editado_por e o que diz que o lancamento nao e mais o que a loja
        mandou — o turno e tao parte do lancamento quanto o valor."""
        fechamento = self.lancado(periodo="MANHA")

        self.corrigir(fechamento, periodo="TARDE")

        fechamento.refresh_from_db()
        self.assertEqual(fechamento.editado_por, self.gerente)

    def test_dia_comum_nao_aceita_domingo_nem_feriado(self):
        fechamento = self.lancado(periodo="MANHA")

        for turno in ("DOMINGO", "DIA"):
            with self.subTest(turno=turno):
                resp = self.corrigir(fechamento, periodo=turno)
                self.assertEqual(resp.status_code, 400)
        fechamento.refresh_from_db()
        self.assertEqual(fechamento.periodo, "MANHA")

    def test_domingo_so_tem_o_turno_de_domingo(self):
        fechamento = self.lancado(data=UM_DOMINGO, periodo="DOMINGO")

        resp = self.corrigir(fechamento, periodo="MANHA")

        self.assertEqual(resp.status_code, 400)

    def test_no_feriado_a_manha_vira_o_turno_do_feriado(self):
        """O lancamento que entrou como manha num feriado — o formulario
        antigo deixava — se conserta para o turno do dia inteiro."""
        fechamento = self.lancado(data=SETE_DE_SETEMBRO, periodo="MANHA")

        resp = self.corrigir(fechamento, periodo="DIA")

        self.assertEqual(resp.status_code, 200, resp.data)

    def test_no_feriado_nao_da_para_escolher_a_tarde(self):
        fechamento = self.lancado(data=SETE_DE_SETEMBRO, periodo="DIA")

        resp = self.corrigir(fechamento, periodo="TARDE")

        self.assertEqual(resp.status_code, 400)

    def test_feriado_local_cadastrado_depois_tambem_vale(self):
        """A padroeira entrou no cadastro depois que a loja ja tinha mandado a
        manha: a correcao segue o calendario de hoje da empresa."""
        fechamento = self.lancado(periodo="MANHA")
        Feriado.objects.create(conta=self.conta, data=DIA_COMUM, descricao="Padroeira")

        resp = self.corrigir(fechamento, periodo="DIA")

        self.assertEqual(resp.status_code, 200, resp.data)

    def test_empresa_de_um_fechamento_por_dia_so_tem_o_dia(self):
        self.conta.fechamentos_por_dia = 1
        self.conta.save()
        fechamento = self.lancado(periodo="DIA")

        resp = self.corrigir(fechamento, periodo="MANHA")

        self.assertEqual(resp.status_code, 400)

    def test_repetir_o_turno_atual_nao_e_trocar(self):
        """Uma manha lancada antes do feriado local entrar no cadastro ficou
        num turno que hoje nao existe naquele dia. Corrigir o PIX dela nao pode
        travar por isso — o turno nao mudou."""
        fechamento = self.lancado(periodo="MANHA")
        Feriado.objects.create(conta=self.conta, data=DIA_COMUM, descricao="Padroeira")

        resp = self.corrigir(fechamento, periodo="MANHA", pix="90.00")

        self.assertEqual(resp.status_code, 200, resp.data)

    def test_o_turno_de_destino_ocupado_nao_trava_a_troca(self):
        """Os dois da loja trocaram os turnos entre si: a manha esta na tarde e
        a tarde na manha. Se o turno ocupado travasse, nenhum dos dois saia do
        lugar. A tela avisa antes; o servidor deixa."""
        manha_errada = self.lancado(periodo="TARDE")
        self.lancado(periodo="MANHA")

        resp = self.corrigir(manha_errada, periodo="MANHA")

        self.assertEqual(resp.status_code, 200, resp.data)

    def test_a_data_nao_se_troca_pela_correcao(self):
        """Mudar o dia nao e corrigir o lancamento, e move-lo para outro
        lugar do painel — e passava sem regra nenhuma de turno."""
        fechamento = self.lancado(periodo="MANHA")

        self.corrigir(fechamento, data="2026-08-04")

        fechamento.refresh_from_db()
        self.assertEqual(str(fechamento.data), DIA_COMUM)
