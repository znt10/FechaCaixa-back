import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase

from app.services.pluggy.transacoes import linha_da_transacao

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "pluggy" / "transacoes.json").read_text(
        encoding="utf-8"
    )
)


def com_descricao(texto):
    return linha_da_transacao({**FIXTURE["sem_pagamento"], "description": texto})


class TransacaoPluggyTests(SimpleTestCase):
    def test_pix_de_madrugada_utc_e_do_dia_anterior_em_brasilia(self):
        linha = linha_da_transacao(FIXTURE["madrugada"])
        self.assertEqual(linha.data, date(2026, 10, 17))

    def test_pix_de_tarde_continua_no_mesmo_dia(self):
        self.assertEqual(
            linha_da_transacao(FIXTURE["saida_pix"]).data, date(2026, 10, 18)
        )

    def test_valor_com_centavos_sem_residuo(self):
        linha = linha_da_transacao(FIXTURE["centavos"])
        self.assertEqual(linha.valor, Decimal("-510.11"))
        self.assertEqual(linha.valor.as_tuple().exponent, -2)

    def test_valor_inteiro_ganha_duas_casas(self):
        linha = linha_da_transacao(FIXTURE["entrada_pix"])
        self.assertEqual(str(linha.valor), "1500.00")

    def test_pendente_fica_de_fora(self):
        self.assertIsNone(linha_da_transacao(FIXTURE["pendente"]))

    def test_saida_usa_quem_recebeu(self):
        linha = linha_da_transacao(FIXTURE["saida_pix"])
        self.assertEqual(linha.contraparte_nome, "Fulana de Tal")
        self.assertEqual(linha.contraparte_documento, "12345678901")
        self.assertEqual(linha.contraparte_conta, "1065391005")

    def test_entrada_usa_quem_pagou(self):
        linha = linha_da_transacao(FIXTURE["entrada_pix"])
        self.assertEqual(linha.contraparte_nome, "Beltrano da Silva")
        self.assertEqual(linha.contraparte_documento, "98765432100")
        self.assertEqual(linha.contraparte_conta, "555123")

    def test_sem_payment_data_entra_sem_contraparte(self):
        linha = linha_da_transacao(FIXTURE["sem_pagamento"])
        self.assertEqual(
            (linha.contraparte_nome, linha.contraparte_documento, linha.contraparte_conta),
            ("", "", ""),
        )

    def test_nome_nulo_vira_vazio(self):
        transacao = json.loads(json.dumps(FIXTURE["saida_pix"]))
        transacao["paymentData"]["receiver"]["name"] = None
        self.assertEqual(linha_da_transacao(transacao).contraparte_nome, "")

    def test_id_vai_para_id_externo_e_id_do_banco_fica_vazio(self):
        linha = linha_da_transacao(FIXTURE["saida_pix"])
        self.assertEqual(linha.id_externo, "tx-saida")
        self.assertEqual(linha.id_do_banco, "")

    def test_descricao_troca_a_barra_e_respeita_255(self):
        linha = linha_da_transacao(FIXTURE["saida_pix"])
        self.assertEqual(
            linha.descricao, "Transferência enviada pelo Pix - Fulana de Tal"
        )
        self.assertEqual(len(com_descricao("x" * 300).descricao), 255)
        self.assertEqual(com_descricao("").descricao, "(sem descrição)")
        self.assertEqual(com_descricao(None).descricao, "(sem descrição)")
