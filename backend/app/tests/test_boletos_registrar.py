"""Guardar e ler o boleto: a regra de quem vale mais.

A loja digita o vencimento olhando o papel, e o celular da loja pode tirar
uma foto ruim. Entao o digitado vale mais que o lido; a leitura so completa
o que ficou vazio, e a linha digitavel confere a leitura por conta.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from app.models import Boleto, Conta
from app.services.boletos.registrar import BoletoRepetido, registrar_boleto
from app.services.comprovantes.arquivo import ArquivoInvalido
from app.services.comprovantes.leitura import LeituraIndisponivel

from .test_boletos_linha_digitavel import BANCARIO, CONSUMO_MOD10

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

    def registrar(self, ler, conteudo=PDF, ler_gratis=lambda conteudo, tipo: None, **campos):
        return registrar_boleto(
            self.conta,
            conteudo,
            "application/pdf",
            "boleto.pdf",
            origem=Boleto.Origem.FORMULARIO,
            ler=ler,
            ler_gratis=ler_gratis,
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


def ia_proibida(conteudo, tipo):
    raise AssertionError("a leitura de graca bastava: a IA nao devia ser chamada")


class LeituraDeGracaPrimeiroTests(TestCase):
    """A IA custa: so entra quando o texto do PDF e o codigo de barras falham."""

    def setUp(self):
        self.conta = Conta.objects.create(nome="Aurora")

    def registrar(self, ler, ler_gratis, **campos):
        return registrar_boleto(
            self.conta, PDF, "application/pdf", "boleto.pdf",
            origem=Boleto.Origem.FORMULARIO, ler=ler, ler_gratis=ler_gratis, hoje=HOJE, **campos,
        )

    def test_linha_de_graca_dispensa_a_ia(self):
        boleto = self.registrar(ia_proibida, le(BANCARIO))

        self.assertEqual(boleto.linha_digitavel, BANCARIO)
        self.assertEqual(boleto.vencimento, date(2026, 11, 20))
        self.assertEqual(boleto.valor, Decimal("230.00"))
        self.assertIsNone(boleto.leitura_bruta)

    def test_sem_linha_de_graca_chama_a_ia(self):
        boleto = self.registrar(le(leitura(vencimento="2026-11-25")), le(None))

        self.assertEqual(boleto.vencimento, date(2026, 11, 25))

    def test_conta_de_consumo_ainda_chama_a_ia_para_a_data(self):
        """A linha da conta de luz nao traz vencimento: so o valor sai de graca."""
        boleto = self.registrar(le(leitura(vencimento="2026-11-25")), le(CONSUMO_MOD10))

        self.assertEqual(boleto.vencimento, date(2026, 11, 25))
        self.assertEqual(boleto.valor, Decimal("189.47"))
        self.assertEqual(boleto.linha_digitavel, CONSUMO_MOD10)

    def test_conta_de_consumo_com_data_digitada_dispensa_a_ia(self):
        boleto = self.registrar(ia_proibida, le(CONSUMO_MOD10), vencimento=date(2026, 11, 25))

        self.assertEqual(boleto.valor, Decimal("189.47"))

    def test_leitura_de_graca_que_quebra_nao_perde_o_boleto(self):
        def quebra(conteudo, tipo):
            raise ValueError("pdf estranho")

        boleto = self.registrar(le(leitura(vencimento="2026-11-25")), quebra)

        self.assertEqual(boleto.vencimento, date(2026, 11, 25))


class BoletoRepetidoTests(TestCase):
    """Como o comprovante: o mesmo boleto nao entra duas vezes."""

    def setUp(self):
        self.conta = Conta.objects.create(nome="Aurora")

    def registrar(self, conteudo, ler=nao_le, ler_gratis=lambda conteudo, tipo: None, conta=None):
        return registrar_boleto(
            conta or self.conta, conteudo, "application/pdf", "boleto.pdf",
            origem=Boleto.Origem.FORMULARIO, ler=ler, ler_gratis=ler_gratis, hoje=HOJE,
        )

    def test_mesmo_arquivo_duas_vezes_e_recusado(self):
        self.registrar(PDF)

        with self.assertRaises(BoletoRepetido):
            self.registrar(PDF)
        self.assertEqual(Boleto.objects.count(), 1)

    def test_mesma_linha_em_outro_arquivo_e_recusada(self):
        self.registrar(PDF, ler_gratis=le(BANCARIO))

        with self.assertRaises(BoletoRepetido):
            self.registrar(b"%PDF-1.4 outra foto", ler_gratis=le(BANCARIO))
        self.assertEqual(Boleto.objects.count(), 1)

    def test_mesma_linha_lida_pela_ia_tambem_e_recusada(self):
        self.registrar(PDF, ler=le(leitura(linha_digitavel=BANCARIO)))

        with self.assertRaises(BoletoRepetido):
            self.registrar(b"%PDF-1.4 outra foto", ler=le(leitura(linha_digitavel=BANCARIO)))
        self.assertEqual(Boleto.objects.count(), 1)

    def test_pago_tambem_conta_como_repetido(self):
        primeiro = self.registrar(PDF, ler_gratis=le(BANCARIO))
        primeiro.situacao = Boleto.Situacao.PAGO
        primeiro.save()

        with self.assertRaises(BoletoRepetido):
            self.registrar(b"%PDF-1.4 outra foto", ler_gratis=le(BANCARIO))

    def test_descartado_nao_conta(self):
        """Descartou por engano: mandar de novo tem que entrar."""
        primeiro = self.registrar(PDF, ler_gratis=le(BANCARIO))
        primeiro.situacao = Boleto.Situacao.DESCARTADO
        primeiro.save()

        self.registrar(PDF, ler_gratis=le(BANCARIO))

        self.assertEqual(Boleto.objects.count(), 2)

    def test_outra_empresa_nao_conta(self):
        self.registrar(PDF, ler_gratis=le(BANCARIO))

        self.registrar(PDF, ler_gratis=le(BANCARIO), conta=Conta.objects.create(nome="Rival"))

        self.assertEqual(Boleto.objects.count(), 2)

    def test_mesmo_valor_e_vencimento_sem_linha_marca_conferir(self):
        """Foto diferente do mesmo boleto sem linha legivel: pode ser repetido,
        mas nao da para ter certeza. A gerencia olha."""
        self.registrar(PDF, ler=le(leitura(vencimento="2026-11-20", valor="230.00")))

        segundo = self.registrar(
            b"%PDF-1.4 outra foto", ler=le(leitura(vencimento="2026-11-20", valor="230.00"))
        )

        self.assertIs(segundo.conferir, True)
