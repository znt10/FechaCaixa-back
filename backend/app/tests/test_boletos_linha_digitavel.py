from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from app.services.boletos.linha_digitavel import LinhaDigitavel, ler_linha

# Linhas inventadas, com os verificadores calculados: nenhum boleto real entra
# no repositorio, que tem espelho publico.
HOJE = date(2026, 11, 2)
BANCARIO = "34191234546789012345767890123457316360000023000"  # 20/11/2026, R$ 230,00
BANCARIO_SEM_VENCIMENTO = "23795555565555544444144444333338200000000005000"  # fator 0000, R$ 50,00
CONSUMO_MOD10 = "836300000012894700000009011111111115111111111113"  # R$ 189,47
CONSUMO_MOD11 = "838400000006573000000008022222222221222222222224"  # R$ 57,30
CONSUMO_REFERENCIA = "837100000012894700000009011111111115111111111113"  # 3o digito 7


class LinhaDigitavelTests(SimpleTestCase):
    def test_bancario_da_vencimento_e_valor(self):
        linha = ler_linha(BANCARIO, HOJE)

        self.assertEqual(
            (linha.tipo, linha.vencimento, linha.valor),
            ("bancario", date(2026, 11, 20), Decimal("230.00")),
        )
        self.assertEqual(linha.digitos, BANCARIO)

    def test_aceita_a_linha_como_esta_impressa(self):
        linha = ler_linha("34191.23454 67890.123457 67890.123457 3 16360000023000", HOJE)

        self.assertEqual(linha.digitos, BANCARIO)

    def test_digito_trocado_e_recusado(self):
        self.assertIsNone(ler_linha(BANCARIO[:-1] + "1", HOJE))  # DV geral
        self.assertIsNone(ler_linha(BANCARIO[:19] + "6" + BANCARIO[20:], HOJE))  # DV do campo 2

    def test_fator_vale_a_data_mais_perto_de_hoje(self):
        """O fator recomecou em 1000 em 22/02/2025: o mesmo numero serve aos
        dois ciclos, e o boleto de agora e o do ciclo mais perto de hoje."""
        self.assertEqual(ler_linha(BANCARIO, date(2002, 3, 20)).vencimento, date(2002, 3, 31))

    def test_fator_zero_e_sem_vencimento(self):
        linha = ler_linha(BANCARIO_SEM_VENCIMENTO, HOJE)

        self.assertEqual((linha.vencimento, linha.valor), (None, Decimal("50.00")))

    def test_conta_de_consumo_da_so_o_valor(self):
        self.assertEqual(
            ler_linha(CONSUMO_MOD10, HOJE),
            LinhaDigitavel(CONSUMO_MOD10, "consumo", None, Decimal("189.47")),
        )
        self.assertEqual(ler_linha(CONSUMO_MOD11, HOJE).valor, Decimal("57.30"))

    def test_conta_de_consumo_de_referencia_nao_tem_valor(self):
        self.assertIsNone(ler_linha(CONSUMO_REFERENCIA, HOJE).valor)

    def test_vazio_ou_tamanho_errado(self):
        for texto in (None, "", "123", BANCARIO + "0"):
            with self.subTest(texto=texto):
                self.assertIsNone(ler_linha(texto, HOJE))
