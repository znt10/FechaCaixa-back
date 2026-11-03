import json
import os
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from app.services.boletos.leitura import CAMPOS, ler_boleto
from app.services.comprovantes.leitura import LeituraIndisponivel

LEITURA = {"e_boleto": True, "varios_boletos": False, **{campo: None for campo in CAMPOS}, "valor": "230.00"}


def resposta(texto, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=texto)]
    )


class SemChaveTests(SimpleTestCase):
    @mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""})
    def test_sem_chave_levanta_indisponivel(self):
        with self.assertRaises(LeituraIndisponivel):
            ler_boleto(b"x", "image/jpeg")


@mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "chave-de-teste"})
class LerBoletoTests(SimpleTestCase):
    @mock.patch("app.services.boletos.leitura.anthropic.Anthropic")
    def test_pdf_vai_como_document_e_imagem_como_image(self, cliente):
        criar = cliente.return_value.messages.create
        criar.return_value = resposta(json.dumps(LEITURA))

        ler_boleto(b"%PDF", "application/pdf")
        self.assertEqual(criar.call_args.kwargs["messages"][0]["content"][0]["type"], "document")

        ler_boleto(b"x", "image/jpeg")
        pedido = criar.call_args.kwargs
        self.assertEqual(pedido["messages"][0]["content"][0]["type"], "image")
        self.assertEqual(pedido["model"], "claude-haiku-5-5")
        esquema = pedido["output_config"]["format"]["schema"]
        self.assertEqual(esquema["required"][:2], ["e_boleto", "varios_boletos"])

    @mock.patch("app.services.boletos.leitura.anthropic.Anthropic")
    def test_resposta_vira_dict(self, cliente):
        cliente.return_value.messages.create.return_value = resposta(json.dumps(LEITURA))

        self.assertEqual(ler_boleto(b"x", "image/jpeg"), LEITURA)

    @mock.patch("app.services.boletos.leitura.anthropic.Anthropic")
    def test_parada_antes_do_fim_levanta_indisponivel(self, cliente):
        cliente.return_value.messages.create.return_value = resposta('{"valor": "2', "max_tokens")

        with self.assertRaises(LeituraIndisponivel):
            ler_boleto(b"x", "image/jpeg")
