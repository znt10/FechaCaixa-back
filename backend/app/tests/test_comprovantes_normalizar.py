from decimal import Decimal

from django.test import SimpleTestCase
from django.utils import timezone

from app.services.comprovantes.normalizar import (
    data_hora,
    mesma_conta,
    normalizar_nome,
    so_digitos,
    valor_decimal,
)


class NormalizarTests(SimpleTestCase):
    def test_so_digitos(self):
        self.assertEqual(so_digitos("11.222.333/0001-81"), "11222333000181")
        self.assertEqual(so_digitos(None), "")
        # Mascarado vira pedaco curto, que nunca bate com CNPJ de 14 digitos.
        self.assertEqual(so_digitos("52.******/****-28"), "5228")

    def test_nome_ignora_caixa_acento_e_espaco(self):
        self.assertEqual(normalizar_nome("  Efí  s.a. - ip "), "EFI S.A. - IP")
        self.assertEqual(normalizar_nome(None), "")

    def test_valor_com_ponto_decimal(self):
        self.assertEqual(valor_decimal("129.80"), Decimal("129.80"))

    def test_valor_no_formato_brasileiro(self):
        # Foco da revisao 2: o esquema pede ponto, mas o modelo pode copiar a tela.
        self.assertEqual(valor_decimal("R$ 1.234,56"), Decimal("1234.56"))
        self.assertEqual(valor_decimal("15,00"), Decimal("15.00"))

    def test_valor_invalido_ou_zero_vira_none(self):
        self.assertIsNone(valor_decimal(None))
        self.assertIsNone(valor_decimal(""))
        self.assertIsNone(valor_decimal("abc"))
        self.assertIsNone(valor_decimal("0.00"))

    def test_data_hora_ganha_o_fuso_de_sao_paulo(self):
        momento = data_hora("2026-10-13T10:03:22")
        self.assertTrue(timezone.is_aware(momento))
        self.assertEqual(timezone.localtime(momento).hour, 10)

    def test_data_hora_invalida_vira_none(self):
        self.assertIsNone(data_hora(None))
        self.assertIsNone(data_hora("13 OUT 2026"))

    def test_mesma_conta_ignora_pontuacao_e_zero_a_esquerda(self):
        self.assertTrue(mesma_conta("3991", "13005228-1", "03991", "130052281"))
        self.assertFalse(mesma_conta("3991", "13005228-1", "3991", "13005228-2"))
        self.assertFalse(mesma_conta("3991", "", "3991", ""))


class ValorAmbiguoTests(SimpleTestCase):
    """Revisao final: valor errado somava como OK sem aviso."""

    def test_virgula_de_milhar_no_formato_americano(self):
        self.assertEqual(valor_decimal("1,234.56"), Decimal("1234.56"))

    def test_tres_digitos_depois_do_separador_e_milhar(self):
        # Dinheiro nao tem tres casas: "1.500" e mil e quinhentos.
        self.assertEqual(valor_decimal("1.500"), Decimal("1500.00"))
        self.assertEqual(valor_decimal("R$ 1.500"), Decimal("1500.00"))
        self.assertEqual(valor_decimal("1,500"), Decimal("1500.00"))
        self.assertEqual(valor_decimal("1.234.567,89"), Decimal("1234567.89"))

    def test_uma_casa_decimal(self):
        self.assertEqual(valor_decimal("12.5"), Decimal("12.50"))

    def test_separadores_sem_sentido_viram_none(self):
        self.assertIsNone(valor_decimal("1.2.3"))
        self.assertIsNone(valor_decimal("1,2,3"))

    def test_valor_que_nao_cabe_no_campo_vira_none(self):
        # Linha digitavel de boleto lida como valor estouraria o DECIMAL(12,2) do MySQL.
        self.assertIsNone(valor_decimal("12345678901.00"))
