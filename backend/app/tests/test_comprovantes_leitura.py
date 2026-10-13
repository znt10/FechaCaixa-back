import io
import json
import os
from types import SimpleNamespace
from unittest import mock

import anthropic
from django.test import SimpleTestCase
from PIL import Image

from app.services.comprovantes.arquivo import ArquivoInvalido, preparar_arquivo
from app.services.comprovantes.leitura import CAMPOS, LeituraIndisponivel, ler_arquivo

LEITURA = {"e_comprovante": True, **{campo: None for campo in CAMPOS}, "valor": "15.00"}


def png(largura, altura):
    saida = io.BytesIO()
    Image.new("RGBA", (largura, altura), (255, 0, 0, 255)).save(saida, format="PNG")
    return saida.getvalue()


def resposta(texto, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=texto)]
    )


class PrepararArquivoTests(SimpleTestCase):
    def test_pdf_passa_como_veio(self):
        self.assertEqual(
            preparar_arquivo(b"%PDF-1.4 x", "application/pdf"),
            (b"%PDF-1.4 x", "application/pdf"),
        )

    def test_foto_grande_vira_jpeg_de_no_maximo_1568(self):
        conteudo, tipo = preparar_arquivo(png(3000, 1000), "image/png")
        self.assertEqual(tipo, "image/jpeg")
        self.assertEqual(max(Image.open(io.BytesIO(conteudo)).size), 1568)

    def test_imagem_que_nao_abre_e_arquivo_invalido(self):
        with self.assertRaises(ArquivoInvalido):
            preparar_arquivo(b"nao sou imagem", "image/png")


@mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "chave-de-teste"})
class LerArquivoTests(SimpleTestCase):
    @mock.patch("app.services.comprovantes.leitura.anthropic.Anthropic")
    def test_devolve_o_json_lido(self, cliente):
        cliente.return_value.messages.create.return_value = resposta(json.dumps(LEITURA))
        self.assertEqual(ler_arquivo(b"x", "image/jpeg")["valor"], "15.00")

    @mock.patch("app.services.comprovantes.leitura.anthropic.Anthropic")
    def test_pdf_vai_como_document_e_imagem_como_image(self, cliente):
        criar = cliente.return_value.messages.create
        criar.return_value = resposta(json.dumps(LEITURA))

        ler_arquivo(b"%PDF", "application/pdf")
        self.assertEqual(criar.call_args.kwargs["messages"][0]["content"][0]["type"], "document")

        ler_arquivo(b"x", "image/jpeg")
        pedido = criar.call_args.kwargs
        self.assertEqual(pedido["messages"][0]["content"][0]["type"], "image")
        self.assertEqual(pedido["model"], "claude-haiku-5-5")
        self.assertEqual(pedido["output_config"]["effort"], "low")
        self.assertEqual(pedido["output_config"]["format"]["type"], "json_schema")

    @mock.patch("app.services.comprovantes.leitura.anthropic.Anthropic")
    def test_api_fora_do_ar_vira_leitura_indisponivel(self, cliente):
        cliente.return_value.messages.create.side_effect = anthropic.APIConnectionError(
            request=mock.Mock()
        )
        with self.assertRaises(LeituraIndisponivel):
            ler_arquivo(b"x", "image/jpeg")

    @mock.patch("app.services.comprovantes.leitura.anthropic.Anthropic")
    def test_resposta_cortada_vira_leitura_indisponivel(self, cliente):
        cliente.return_value.messages.create.return_value = resposta('{"valor": "1', "max_tokens")
        with self.assertRaises(LeituraIndisponivel):
            ler_arquivo(b"x", "image/jpeg")

    @mock.patch("app.services.comprovantes.leitura.anthropic.Anthropic")
    def test_recusa_vira_leitura_indisponivel(self, cliente):
        cliente.return_value.messages.create.return_value = resposta("", "refusal")
        with self.assertRaises(LeituraIndisponivel):
            ler_arquivo(b"x", "image/jpeg")


class SemChaveTests(SimpleTestCase):
    @mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""})
    def test_sem_chave_a_leitura_esta_desligada(self):
        with self.assertRaisesMessage(LeituraIndisponivel, "leitura desligada"):
            ler_arquivo(b"x", "image/jpeg")
