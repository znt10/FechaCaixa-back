"""A leitura de graca, antes da IA: o texto do PDF e o codigo de barras da foto.

Os dois dao a linha digitavel inteira, que no boleto bancario ja traz o
vencimento e o valor. A IA custa a cada leitura; so entra quando isto falha.
Nada aqui levanta: um arquivo estranho e so "nao achei".
"""

import io
import logging
import re
from datetime import date

import pypdf
import zxingcpp
from PIL import Image, ImageOps

from .linha_digitavel import ler_linha, linha_da_barra

logger = logging.getLogger(__name__)

# A linha impressa: digitos com pontos e espacos, numa linha so do texto.
TRECHO_COM_DIGITOS = re.compile(r"[\d][\d.\s-]{45,70}[\d]")


def _linha_no_texto(texto):
    for trecho in TRECHO_COM_DIGITOS.findall(texto):
        digitos = re.sub(r"\D", "", trecho)
        # Validada aqui so para escolher o trecho certo; quem decide a data e o
        # registro, com o "hoje" dele.
        if ler_linha(digitos, date.today()) is not None:
            return digitos
    return None


def _do_pdf(conteudo):
    leitor = pypdf.PdfReader(io.BytesIO(conteudo))
    for pagina in leitor.pages[:3]:
        linha = _linha_no_texto(pagina.extract_text() or "")
        if linha:
            return linha
    return None


def _da_foto(conteudo):
    imagem = ImageOps.exif_transpose(Image.open(io.BytesIO(conteudo))).convert("L")
    for codigo in zxingcpp.read_barcodes(imagem, formats=zxingcpp.BarcodeFormat.ITF):
        linha = linha_da_barra(codigo.text)
        if linha:
            return linha
    return None


def ler_sem_ia(conteudo, tipo):
    """A linha digitavel (so digitos) achada sem IA, ou None."""
    try:
        if tipo == "application/pdf":
            return _do_pdf(conteudo)
        return _da_foto(conteudo)
    except Exception as erro:
        logger.info("Leitura sem IA nao achou a linha: %s", erro)
        return None
