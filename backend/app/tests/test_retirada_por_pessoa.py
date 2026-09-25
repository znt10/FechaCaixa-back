"""A retirada: uma linha por pessoa, no mesmo envio do fechamento.

Ja foi um par de campos (`responsavel_retirada` e `valor_retirado`), e cabia uma
pessoa por turno. Quando o dono e a socia retiram no mesmo expediente, o
segundo valor nao tinha onde entrar.

Os campos antigos ficam como resumo das linhas — a soma e a primeira pessoa —
porque o total do caixa, os graficos e a planilha leem `valor_retirado`. O que
este arquivo mais protege e que o resumo nunca diverge das linhas.
"""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from rest_framework.test import APIClient, APITestCase

from app.models import Conta, Encarregado, FechamentoCaixa, ResponsavelRetirada, Retirada

from .fabricas import DIA_COMUM, criar_loja, vincular_conta
from .test_lancamento_pelo_painel import dia_comum_recente

URL = "/api/v1/fechamentos-caixa/"


class BaseDaRetirada(APITestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = APIClient()
        self.conta = Conta.objects.create(nome="Marina")
        self.loja = criar_loja(
            nome_loja="Loja Centro", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.ana = Encarregado.objects.create(nome="Ana Paula", conta=self.conta)
        self.marina = ResponsavelRetirada.objects.create(nome="Marina", conta=self.conta)
        self.juselino = ResponsavelRetirada.objects.create(
            nome="Juselino", conta=self.conta
        )
        self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": self.conta.codigo_acesso, "apelido": "Caixa"},
            format="json",
        )

    def retirada(self, pessoa, valor):
        return {"responsavel": str(pessoa.public_id), "valor": valor}

    def payload(self, **extra):
        base = {
            "loja": str(self.loja.public_id),
            "lancado_por": str(self.ana.public_id),
            "data": DIA_COMUM,
            "periodo": "TARDE",
            "pix": "100.00",
            "cartao": "0.00",
            "dinheiro": "50.00",
            "link_pagamento": "0.00",
            "houve_retirada": False,
        }
        base.update(extra)
        return base

    def lancar(self, **extra):
        resp = self.client.post(URL, self.payload(**extra), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        return FechamentoCaixa.objects.get(public_id=resp.data["id"])

    def linhas(self):
        return [
            (r.responsavel.nome if r.responsavel else None, r.valor)
            for r in Retirada.objects.select_related("responsavel").order_by("id")
        ]


class LancarRetiradaTests(BaseDaRetirada):
    def test_duas_pessoas_retiram_no_mesmo_turno(self):
        fechamento = self.lancar(
            houve_retirada=True,
            retiradas=[
                self.retirada(self.marina, "300.00"),
                self.retirada(self.juselino, "200.00"),
            ],
        )

        self.assertEqual(
            self.linhas(),
            [("Marina", Decimal("300.00")), ("Juselino", Decimal("200.00"))],
        )
        # O resumo e o que o total le: a soma das duas, nao so a primeira.
        self.assertTrue(fechamento.houve_retirada)
        self.assertEqual(fechamento.valor_retirado, Decimal("500.00"))
        self.assertEqual(fechamento.responsavel_retirada, self.marina)
        self.assertEqual(fechamento.total, Decimal("650.00"))

    def test_o_par_antigo_ainda_vira_uma_linha(self):
        """O app das lojas sobe depois do backend: nesse meio tempo ele manda o
        par, e a retirada do turno nao pode sumir."""
        fechamento = self.lancar(
            houve_retirada=True,
            responsavel_retirada=str(self.marina.public_id),
            valor_retirado="300.00",
        )

        self.assertEqual(self.linhas(), [("Marina", Decimal("300.00"))])
        self.assertEqual(fechamento.valor_retirado, Decimal("300.00"))

    def test_sim_sem_ninguem_e_recusado(self):
        resp = self.client.post(
            URL, self.payload(houve_retirada=True), format="json"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(FechamentoCaixa.objects.count(), 0)

    def test_linha_sem_valor_e_recusada(self):
        resp = self.client.post(
            URL,
            self.payload(
                houve_retirada=True,
                retiradas=[self.retirada(self.marina, "0.00")],
            ),
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Retirada.objects.count(), 0)

    def test_pessoa_de_outra_empresa_e_recusada(self):
        vizinha = Conta.objects.create(nome="Vizinha")
        de_fora = ResponsavelRetirada.objects.create(nome="Intruso", conta=vizinha)

        resp = self.client.post(
            URL,
            self.payload(
                houve_retirada=True,
                retiradas=[
                    self.retirada(self.marina, "10.00"),
                    self.retirada(de_fora, "10.00"),
                ],
            ),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("retiradas", resp.data)
        self.assertEqual(FechamentoCaixa.objects.count(), 0)

    def test_nao_a_pergunta_nao_grava_linha(self):
        fechamento = self.lancar(houve_retirada=False, retiradas=[])

        self.assertEqual(Retirada.objects.count(), 0)
        self.assertFalse(fechamento.houve_retirada)
        self.assertIsNone(fechamento.valor_retirado)


class CorrigirRetiradaPelaLojaTests(BaseDaRetirada):
    def url_de_correcao(self, fechamento):
        return f"{URL}{fechamento.public_id}/correcao/"

    def test_o_formulario_reabre_com_as_duas_pessoas(self):
        fechamento = self.lancar(
            houve_retirada=True,
            retiradas=[
                self.retirada(self.marina, "300.00"),
                self.retirada(self.juselino, "200.00"),
            ],
        )

        resp = self.client.get(self.url_de_correcao(fechamento))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            [(r["nome"], str(r["valor"])) for r in resp.data["retiradas"]],
            [("Marina", "300.00"), ("Juselino", "200.00")],
        )

    def test_corrigir_troca_a_lista_e_o_resumo(self):
        fechamento = self.lancar(
            houve_retirada=True,
            retiradas=[self.retirada(self.marina, "300.00")],
        )

        resp = self.client.patch(
            self.url_de_correcao(fechamento),
            self.payload(
                houve_retirada=True,
                retiradas=[
                    self.retirada(self.marina, "100.00"),
                    self.retirada(self.juselino, "50.00"),
                ],
            ),
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(
            self.linhas(),
            [("Marina", Decimal("100.00")), ("Juselino", Decimal("50.00"))],
        )
        fechamento.refresh_from_db()
        self.assertEqual(fechamento.valor_retirado, Decimal("150.00"))

    def test_corrigir_sem_falar_de_retirada_preserva_as_linhas(self):
        """Consertar o PIX nao pode apagar a retirada de tabela."""
        fechamento = self.lancar(
            houve_retirada=True,
            retiradas=[
                self.retirada(self.marina, "300.00"),
                self.retirada(self.juselino, "200.00"),
            ],
        )

        payload = self.payload(pix="180.00")
        payload.pop("houve_retirada")

        resp = self.client.patch(
            self.url_de_correcao(fechamento), payload, format="json"
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(len(self.linhas()), 2)
        fechamento.refresh_from_db()
        self.assertEqual(fechamento.valor_retirado, Decimal("500.00"))

    def test_desligar_a_pergunta_apaga_as_linhas(self):
        fechamento = self.lancar(
            houve_retirada=True,
            retiradas=[self.retirada(self.marina, "300.00")],
        )

        self.client.patch(
            self.url_de_correcao(fechamento),
            self.payload(houve_retirada=False, retiradas=[]),
            format="json",
        )

        self.assertEqual(Retirada.objects.count(), 0)
        fechamento.refresh_from_db()
        self.assertFalse(fechamento.houve_retirada)
        self.assertIsNone(fechamento.valor_retirado)


class CorrigirRetiradaPeloPainelTests(APITestCase):
    def setUp(self):
        for nome in ("Admin", "Gerente", "Responsavel"):
            Group.objects.get_or_create(name=nome)

        self.conta = Conta.objects.create(nome="Marina")
        self.gerente = User.objects.create_user(username="ger@x.com", password="123456")
        self.gerente.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(self.gerente, self.conta)

        self.loja = criar_loja(
            nome_loja="Loja A", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.marina = ResponsavelRetirada.objects.create(nome="Marina", conta=self.conta)
        self.juselino = ResponsavelRetirada.objects.create(
            nome="Juselino", conta=self.conta
        )
        self.fechamento = FechamentoCaixa.objects.create(
            loja=self.loja, nome_funcionario="Ana", data=DIA_COMUM,
            periodo="TARDE", pix=100, cartao=0, dinheiro=0, link_pagamento=0,
            houve_retirada=True, responsavel_retirada=self.marina,
            valor_retirado=Decimal("300.00"),
        )
        Retirada.objects.create(
            fechamento=self.fechamento, responsavel=self.marina, valor=Decimal("300.00")
        )

        self.client = APIClient()
        self.client.force_authenticate(self.gerente)

    def url(self):
        return f"{URL}{self.fechamento.public_id}/"

    def test_gerencia_acrescenta_a_segunda_pessoa(self):
        resp = self.client.patch(
            self.url(),
            {
                "houve_retirada": True,
                "retiradas": [
                    {"responsavel": str(self.marina.public_id), "valor": "300.00"},
                    {"responsavel": str(self.juselino.public_id), "valor": "80.00"},
                ],
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        # A resposta do PATCH e igual a da listagem: o painel troca a linha.
        self.assertEqual(
            [(r["nome"], str(r["valor"])) for r in resp.data["retiradas"]],
            [("Marina", "300.00"), ("Juselino", "80.00")],
        )
        self.assertEqual(str(resp.data["valor_retirado"]), "380.00")
        self.assertEqual(str(resp.data["total"]), "480.00")

    def test_a_listagem_devolve_as_linhas(self):
        resp = self.client.get(URL, {"data": DIA_COMUM})

        self.assertEqual(resp.status_code, 200)
        linha = resp.data["results"][0] if isinstance(resp.data, dict) else resp.data[0]
        self.assertEqual(
            [(r["nome"], str(r["valor"])) for r in linha["retiradas"]],
            [("Marina", "300.00")],
        )

    def test_corrigir_o_pix_nao_mexe_na_retirada(self):
        resp = self.client.patch(self.url(), {"pix": "180.00"}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Retirada.objects.count(), 1)
        self.fechamento.refresh_from_db()
        self.assertEqual(self.fechamento.valor_retirado, Decimal("300.00"))

    def test_lancar_pelo_painel_com_duas_pessoas(self):
        """A reposicao da tela da empresa manda a mesma lista que o formulario."""
        dia = dia_comum_recente(self.conta)
        resp = self.client.post(
            URL,
            {
                "loja": str(self.loja.public_id),
                "lancado_por": None,
                "data": dia.isoformat(),
                "periodo": "TARDE",
                "pix": "0.00", "cartao": "0.00", "dinheiro": "10.00",
                "link_pagamento": "0.00",
                "houve_retirada": True,
                "retiradas": [
                    {"responsavel": str(self.marina.public_id), "valor": "40.00"},
                    {"responsavel": str(self.juselino.public_id), "valor": "60.00"},
                ],
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        fechamento = FechamentoCaixa.objects.get(public_id=resp.data["id"])
        self.assertEqual(fechamento.retiradas.count(), 2)
        self.assertEqual(fechamento.valor_retirado, Decimal("100.00"))
