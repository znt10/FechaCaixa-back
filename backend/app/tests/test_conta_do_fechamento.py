"""A conta do fechamento: o que volta para o total e o que nao volta.

O campo `dinheiro` e o dinheiro que esta FISICAMENTE na gaveta na hora de
fechar — o funcionario conta o que tem na mao. Ele ja esta sem a retirada que
o dono levou e sem o que foi pago de despesa.

Ate 2026-09-01 a conta subtraia os dois de novo, e por isso descontava cada
turno duas vezes: um turno que movimentou 1.200 com 500 retirados aparecia
como 200. O erro era `2 x retirada + despesa`, e crescia justamente quando o
dono fazia a coisa certa — passar recolhendo dinheiro para nao deixar caixa
grande na mao de quem esta no balcao.

A regra que este arquivo protege:

    Volta para o total tudo que saiu da gaveta SEM cancelar uma venda.

    retirada  -> soma    (foi guardar; a venda valeu)
    despesa   -> soma    (o dinheiro sumiu, mas a venda valeu)
    devolucao -> neutra  (saiu da gaveta E cancelou a venda; se anulam)
    consumo   -> neutro  (nenhum dinheiro se moveu)

A devolucao e a que mais convida ao erro. Ela parece que tem que subtrair —
o cliente levou o dinheiro de volta, a venda nao existiu. Mas o dinheiro saiu
da mesma gaveta que o funcionario contou, entao ela JA se descontou sozinha.
Subtrair de novo repete exatamente o bug que esta suite existe para impedir.
"""

from decimal import Decimal

from rest_framework.test import APIClient, APITestCase

from app.models import Conta, Despesa, Encarregado, FechamentoCaixa, ResponsavelRetirada

from .fabricas import DIA_COMUM, criar_loja

URL = "/api/v1/fechamentos-caixa/"


class BaseDaConta(APITestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = APIClient()
        self.conta = Conta.objects.create(nome="Primavera")
        self.loja = criar_loja(
            nome_loja="Loja Centro", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.ana = Encarregado.objects.create(nome="Ana Paula", conta=self.conta)
        self.juselino = ResponsavelRetirada.objects.create(
            nome="Juselino", conta=self.conta
        )
        self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": self.conta.codigo_acesso, "apelido": "Caixa"},
            format="json",
        )

    def payload(self, **extra):
        """Um turno que so teve dinheiro, para a conta ficar legivel.

        350 na gaveta e o que sobrou depois de tudo — e desse numero que as
        outras parcelas voltam.
        """
        base = {
            "loja": str(self.loja.public_id),
            "lancado_por": str(self.ana.public_id),
            "data": DIA_COMUM,
            "periodo": "TARDE",
            "pix": "0.00",
            "cartao": "0.00",
            "dinheiro": "350.00",
            "link_pagamento": "0.00",
            "houve_retirada": False,
            "houve_devolucao": False,
            "houve_desperdicio": False,
        }
        base.update(extra)
        return base

    def retirada(self, valor):
        return {
            "houve_retirada": True,
            "responsavel_retirada": str(self.juselino.public_id),
            "valor_retirado": valor,
        }

    def lancar(self, **extra):
        resp = self.client.post(URL, self.payload(**extra), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        return resp


class RetiradaSomaTests(BaseDaConta):
    def test_a_retirada_volta_para_o_total(self):
        """Ela saiu da gaveta DEPOIS da venda: o dinheiro contado ja esta sem ela.

        Este e o teste que prova o bug corrigido. Com a conta velha o resultado
        seria 350 - 500 = -150, um turno que vendeu aparecendo negativo.
        """
        self.lancar(**self.retirada("500.00"))

        fechamento = FechamentoCaixa.objects.get()
        self.assertEqual(fechamento.total, Decimal("850.00"))

    def test_a_retirada_nao_e_saida_do_caixa(self):
        """Nao e gasto: o dono levou para guardar, o dinheiro continua da empresa."""
        self.lancar(**self.retirada("500.00"))

        fechamento = FechamentoCaixa.objects.get()
        self.assertEqual(fechamento.recebido, Decimal("350.00"))
        self.assertEqual(fechamento.registrado, Decimal("500.00"))


class DespesaSomaTests(BaseDaConta):
    def test_a_despesa_volta_para_o_total(self):
        self.lancar(despesas=[{"descricao": "Gas", "valor": "60.00"}])

        self.assertEqual(FechamentoCaixa.objects.get().total, Decimal("410.00"))

    def test_varias_despesas_no_mesmo_turno(self):
        """O turno gasta com gas, agua e remedio, e sao tres linhas.

        Antes cabia uma so por turno, e o resto ia empilhado num campo de texto.
        """
        self.lancar(
            despesas=[
                {"descricao": "Gas", "valor": "60.00"},
                {"descricao": "Agua", "valor": "40.00"},
                {"descricao": "Remedio", "valor": "12.50"},
            ]
        )

        fechamento = FechamentoCaixa.objects.get()
        self.assertEqual(fechamento.despesas.count(), 3)
        self.assertEqual(fechamento.total_das_despesas, Decimal("112.50"))
        self.assertEqual(fechamento.total, Decimal("462.50"))

    def test_turno_sem_despesa_nao_grava_nada(self):
        self.lancar()

        self.assertEqual(Despesa.objects.count(), 0)


class DevolucaoNaoMexeTests(BaseDaConta):
    """A regra que mais convida ao erro, e a que mais custa errar."""

    def test_a_devolucao_nao_entra_na_conta(self):
        """O dinheiro saiu da gaveta e cancelou a venda junto: os dois se anulam.

        Subtrair aqui faria a loja aparecer vendendo menos do que vendeu — o
        mesmo desconto em dobro que esta mudanca corrigiu na retirada.
        """
        self.lancar(houve_devolucao=True, devolucao_valor="50.00")

        fechamento = FechamentoCaixa.objects.get()
        self.assertEqual(fechamento.total, Decimal("350.00"))

    def test_a_devolucao_fica_registrada(self):
        """Neutra na conta, visivel na tela: a dona quer saber quanto voltou."""
        self.lancar(houve_devolucao=True, devolucao_valor="50.00")

        fechamento = FechamentoCaixa.objects.get()
        self.assertEqual(fechamento.devolucao_valor, Decimal("50.00"))
        self.assertEqual(fechamento.registrado, Decimal("50.00"))


class OTurnoInteiroTests(BaseDaConta):
    def test_o_exemplo_do_dono(self):
        """Gaveta 350, retirada 500, gas 60 + agua 40, devolucao 50.

        A loja movimentou 950: os 350 que sobraram, mais os 500 que o Juselino
        levou, mais os 100 que sairam para pagar gas e agua. A devolucao de 50
        nao entra — ela ja tinha derrubado a gaveta antes da contagem.
        """
        resp = self.lancar(
            **self.retirada("500.00"),
            despesas=[
                {"descricao": "Gas", "valor": "60.00"},
                {"descricao": "Agua", "valor": "40.00"},
            ],
            houve_devolucao=True,
            devolucao_valor="50.00",
        )

        fechamento = FechamentoCaixa.objects.get()
        self.assertEqual(fechamento.total, Decimal("950.00"))
        self.assertEqual(fechamento.registrado, Decimal("650.00"))
        self.assertEqual(str(resp.data["total"]), "950.00")
        self.assertEqual(str(resp.data["registrado"]), "650.00")


class ValidacaoTests(BaseDaConta):
    """Ligar a pergunta e nao preencher e o meio do caminho do formulario."""

    def test_devolucao_ligada_sem_valor_e_recusada(self):
        resp = self.client.post(
            URL, self.payload(houve_devolucao=True), format="json"
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("devolucao_valor", resp.data)

    def test_despesa_sem_valor_e_recusada(self):
        resp = self.client.post(
            URL,
            self.payload(despesas=[{"descricao": "Gas", "valor": "0.00"}]),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_despesa_sem_descricao_e_recusada(self):
        resp = self.client.post(
            URL,
            self.payload(despesas=[{"descricao": "", "valor": "60.00"}]),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
