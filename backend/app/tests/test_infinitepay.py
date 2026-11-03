import io
import json
import urllib.error
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from app.services import infinitepay
from app.services.infinitepay import ErroDaInfinitePay

CONFIGURADA = dict(
    INFINITEPAY_HANDLE="jose",
    INFINITEPAY_API_KEY="",
    INFINITEPAY_WEBHOOK_SEGREDO="s3gr3do",
    FRONTEND_URL="https://fechacaixa.io",
)


class _Resposta(io.BytesIO):
    """O que o `urlopen` devolve, com `with`."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def responde(dados):
    return patch(
        "urllib.request.urlopen",
        return_value=_Resposta(json.dumps(dados).encode()),
    )


def corpo_do_pedido(urlopen):
    return json.loads(urlopen.call_args.args[0].data)


@override_settings(**CONFIGURADA)
class ConfiguracaoTests(SimpleTestCase):
    def test_configurada_precisa_de_handle_e_segredo(self):
        self.assertTrue(infinitepay.configurada())
        with override_settings(INFINITEPAY_WEBHOOK_SEGREDO=""):
            self.assertFalse(infinitepay.configurada())
        with override_settings(INFINITEPAY_HANDLE=""):
            self.assertFalse(infinitepay.configurada())

    def test_segredo_confere(self):
        self.assertTrue(infinitepay.segredo_confere("s3gr3do"))
        self.assertFalse(infinitepay.segredo_confere("x"))
        # Texto nao-ASCII em str levantaria TypeError no compare_digest.
        self.assertFalse(infinitepay.segredo_confere("ção"))
        self.assertFalse(infinitepay.segredo_confere(None))
        with override_settings(INFINITEPAY_WEBHOOK_SEGREDO=""):
            self.assertFalse(infinitepay.segredo_confere(""))


@override_settings(**CONFIGURADA)
class CriarLinkTests(SimpleTestCase):
    def criar(self):
        return infinitepay.criar_link(
            valor_centavos=10000, descricao="Mensalidade FechaCaixa - Padaria", referencia="ref-1"
        )

    def test_criar_link_manda_o_corpo_e_devolve_a_url(self):
        with responde({"url": "https://checkout/x"}) as urlopen:
            self.assertEqual(self.criar(), "https://checkout/x")
        pedido = urlopen.call_args.args[0]
        self.assertEqual(pedido.full_url, "https://api.checkout.infinitepay.io/links")
        self.assertIsNone(pedido.get_header("X-api-key"))
        corpo = corpo_do_pedido(urlopen)
        self.assertEqual(corpo["handle"], "jose")
        self.assertEqual(
            corpo["items"],
            [{"quantity": 1, "price": 10000, "description": "Mensalidade FechaCaixa - Padaria"}],
        )
        self.assertEqual(corpo["order_nsu"], "ref-1")
        self.assertEqual(corpo["redirect_url"], "https://fechacaixa.io/")
        self.assertEqual(
            corpo["webhook_url"],
            "https://fechacaixa.io/backend/api/v1/infinitepay/retorno?t=s3gr3do",
        )

    @override_settings(INFINITEPAY_WEBHOOK_SEGREDO="a+b&c=d#e%f")
    def test_segredo_com_caractere_especial_vai_codificado(self):
        import urllib.parse

        with responde({"url": "https://checkout/x"}) as urlopen:
            self.criar()
        webhook = urllib.parse.urlsplit(corpo_do_pedido(urlopen)["webhook_url"])
        self.assertEqual(urllib.parse.parse_qs(webhook.query)["t"], ["a+b&c=d#e%f"])

    def test_criar_link_aceita_link_no_lugar_de_url(self):
        with responde({"link": "https://checkout/y"}):
            self.assertEqual(self.criar(), "https://checkout/y")

    @override_settings(INFINITEPAY_API_KEY="chave")
    def test_criar_link_com_api_key_manda_o_cabecalho(self):
        with responde({"url": "https://checkout/x"}) as urlopen:
            self.criar()
        self.assertEqual(urlopen.call_args.args[0].get_header("X-api-key"), "chave")

    def test_criar_link_sem_url_na_resposta_levanta(self):
        with responde({"ok": True}), self.assertRaises(ErroDaInfinitePay):
            self.criar()

    def test_http_500_levanta(self):
        erro = urllib.error.HTTPError("u", 500, "erro", {}, io.BytesIO(b"fora"))
        with patch("urllib.request.urlopen", side_effect=erro), self.assertRaises(
            ErroDaInfinitePay
        ):
            self.criar()

    def test_rede_fora_levanta(self):
        with patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("sem rede")
        ), self.assertRaises(ErroDaInfinitePay):
            self.criar()

    def test_resposta_sem_json_levanta(self):
        with patch("urllib.request.urlopen", return_value=_Resposta(b"<html>")), self.assertRaises(
            ErroDaInfinitePay
        ):
            self.criar()


@override_settings(**CONFIGURADA)
class ConferirTests(SimpleTestCase):
    def test_conferir_le_paid_amount_e_meio(self):
        with responde({"paid": True, "amount": 10000, "capture_method": "pix"}) as urlopen:
            r = infinitepay.conferir(referencia="ref-1", transacao="t1", slug="f1")
        self.assertEqual(r, {"pago": True, "valor_centavos": 10000, "meio": "pix"})
        self.assertEqual(
            urlopen.call_args.args[0].full_url,
            "https://api.checkout.infinitepay.io/payment_check",
        )
        self.assertEqual(
            corpo_do_pedido(urlopen),
            {"handle": "jose", "order_nsu": "ref-1", "transaction_nsu": "t1", "slug": "f1"},
        )

    def test_conferir_sem_paid_levanta(self):
        with responde({"amount": 10000}), self.assertRaises(ErroDaInfinitePay):
            infinitepay.conferir(referencia="ref-1", transacao="t1", slug="f1")

    def test_conferir_amount_invalido_levanta(self):
        with responde({"paid": True, "amount": "dez"}), self.assertRaises(ErroDaInfinitePay):
            infinitepay.conferir(referencia="ref-1", transacao="t1", slug="f1")
