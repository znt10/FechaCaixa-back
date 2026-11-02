"""Guardar e ler o boleto: a regra de quem vale mais.

A loja digita o vencimento olhando o papel, e o celular da loja pode tirar
uma foto ruim. Entao o digitado vale mais que o lido; a leitura so completa
o que ficou vazio, e a linha digitavel confere a leitura por conta.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from app.models import Boleto, Conta
from app.services.boletos.registrar import registrar_boleto
from app.services.comprovantes.arquivo import ArquivoInvalido
from app.services.comprovantes.leitura import LeituraIndisponivel

from .test_boletos_linha_digitavel import BANCARIO

HOJE = date(2026, 11, 2)
PDF = b"%PDF-1.4 boleto inventado"


def leitura(**campos):
    base = {
        "e_boleto": True,
        "vencimento": None,
        "valor": None,
        "beneficiario_nome": None,
        "beneficiario_documento": None,
        "linha_digitavel": None,
    }
    return {**base, **campos}


def le(resultado):
    return lambda conteudo, tipo: resultado


def nao_le(conteudo, tipo):
    raise LeituraIndisponivel("leitura desligada")


class RegistrarBoletoTests(TestCase):
    def setUp(self):
        self.conta = Conta.objects.create(nome="Aurora")

    def registrar(self, ler, **campos):
        return registrar_boleto(
            self.conta,
            PDF,
            "application/pdf",
            "boleto.pdf",
            origem=Boleto.Origem.FORMULARIO,
            ler=ler,
            hoje=HOJE,
            **campos,
        )

    def test_grava_mesmo_sem_leitura(self):
        boleto = self.registrar(nao_le)

        boleto.refresh_from_db()
        self.assertEqual(bytes(boleto.arquivo), PDF)
        self.assertIsNone(boleto.vencimento)
        self.assertIs(boleto.conferir, False)
        self.assertIsNone(boleto.leitura_bruta)

    def test_digitado_vale_mais_e_leitura_completa_o_resto(self):
        boleto = self.registrar(
            le(leitura(valor="230.00", beneficiario_nome="Distribuidora Exemplo",
                       linha_digitavel=BANCARIO)),
            vencimento=date(2026, 11, 20),
        )

        self.assertEqual(boleto.vencimento, date(2026, 11, 20))
        self.assertEqual(boleto.valor, Decimal("230.00"))
        self.assertEqual(boleto.beneficiario_nome, "Distribuidora Exemplo")
        self.assertEqual(boleto.campos_editados, ["vencimento"])
        self.assertIs(boleto.conferir, False)

    def test_linha_valida_manda_na_leitura(self):
        boleto = self.registrar(le(leitura(vencimento="2026-11-21", linha_digitavel=BANCARIO)))

        self.assertEqual(boleto.vencimento, date(2026, 11, 20))
        self.assertEqual(boleto.linha_digitavel, BANCARIO)

    def test_digitado_diferente_do_lido_marca_conferir(self):
        boleto = self.registrar(
            le(leitura(linha_digitavel=BANCARIO)), vencimento=date(2026, 12, 20)
        )

        self.assertEqual(boleto.vencimento, date(2026, 12, 20))
        self.assertIs(boleto.conferir, True)

    def test_linha_invalida_nao_e_guardada_e_marca_conferir(self):
        boleto = self.registrar(le(leitura(linha_digitavel=BANCARIO[:-1] + "1")))

        self.assertEqual(boleto.linha_digitavel, "")
        self.assertIs(boleto.conferir, True)

    def test_nao_e_boleto_marca_conferir_e_guarda(self):
        boleto = self.registrar(le(leitura(e_boleto=False)))

        self.assertTrue(Boleto.objects.filter(pk=boleto.pk).exists())
        self.assertIs(boleto.conferir, True)

    def test_valor_no_formato_brasileiro(self):
        boleto = self.registrar(le(leitura(valor="1.234,56")))

        self.assertEqual(boleto.valor, Decimal("1234.56"))

    def test_arquivo_invalido_nao_grava(self):
        with self.assertRaises(ArquivoInvalido):
            registrar_boleto(
                self.conta, b"nao sou imagem", "image/jpeg", "foto.jpg",
                origem=Boleto.Origem.FORMULARIO, ler=nao_le, hoje=HOJE,
            )

        self.assertEqual(Boleto.objects.count(), 0)

    def test_carne_marca_conferir(self):
        """Varios boletos num PDF: le o primeiro, e a gerencia confere o resto."""
        boleto = self.registrar(le(leitura(varios_boletos=True, linha_digitavel=BANCARIO)))

        self.assertIs(boleto.conferir, True)
        self.assertEqual(boleto.vencimento, date(2026, 11, 20))
