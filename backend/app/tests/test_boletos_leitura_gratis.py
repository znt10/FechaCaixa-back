"""A leitura de graca, antes da IA: o texto do PDF e o codigo de barras da foto.

Os dois dao a linha digitavel inteira, que ja traz vencimento e valor do
boleto bancario. A IA so entra quando os dois falham.
"""

import io

import zxingcpp
from django.test import SimpleTestCase
from PIL import Image

from app.services.boletos.leitura_gratis import ler_sem_ia

from .test_boletos_linha_digitavel import BANCARIO, BARRA_BANCARIO


def pdf_com_texto(texto):
    """Um PDF minimo com uma linha de texto em Helvetica, montado a mao."""
    conteudo = f"BT /F1 10 Tf 40 700 Td ({texto}) Tj ET".encode()
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(conteudo)).encode() + b" >>\nstream\n" + conteudo + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    saida = io.BytesIO()
    saida.write(b"%PDF-1.4\n")
    posicoes = []
    for numero, objeto in enumerate(objetos, 1):
        posicoes.append(saida.tell())
        saida.write(f"{numero} 0 obj\n".encode() + objeto + b"\nendobj\n")
    xref = saida.tell()
    saida.write(f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode())
    for posicao in posicoes:
        saida.write(f"{posicao:010d} 00000 n \n".encode())
    saida.write(
        f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    )
    return saida.getvalue()


def png_com_barra(barra):
    imagem = zxingcpp.create_barcode(barra, zxingcpp.BarcodeFormat.ITF).to_image(scale=3)
    pixels = memoryview(imagem)
    altura, largura = pixels.shape
    saida = io.BytesIO()
    Image.frombytes("L", (largura, altura), bytes(pixels)).save(saida, format="PNG")
    return saida.getvalue()


class LerSemIaTests(SimpleTestCase):
    def test_pdf_com_a_linha_no_texto(self):
        pdf = pdf_com_texto("Linha: 34191.23454 67890.123457 67890.123457 3 16360000023000")

        self.assertEqual(ler_sem_ia(pdf, "application/pdf"), BANCARIO)

    def test_foto_com_o_codigo_de_barras(self):
        self.assertEqual(ler_sem_ia(png_com_barra(BARRA_BANCARIO), "image/png"), BANCARIO)

    def test_foto_sem_codigo_de_barras(self):
        saida = io.BytesIO()
        Image.new("RGB", (200, 100), "white").save(saida, format="PNG")

        self.assertIsNone(ler_sem_ia(saida.getvalue(), "image/png"))

    def test_arquivo_quebrado_nao_levanta(self):
        self.assertIsNone(ler_sem_ia(b"%PDF-1.4 quebrado", "application/pdf"))
        self.assertIsNone(ler_sem_ia(b"nao sou imagem", "image/jpeg"))

    def test_pdf_sem_linha_valida(self):
        pdf = pdf_com_texto("Nota fiscal 123456")

        self.assertIsNone(ler_sem_ia(pdf, "application/pdf"))
