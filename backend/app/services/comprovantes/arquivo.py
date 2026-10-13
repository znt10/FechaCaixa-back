"""O que e anexo de comprovante, e como a foto fica antes de ser lida e guardada."""

import io

from PIL import Image, ImageOps

TIPOS_ACEITOS = {"application/pdf", "image/png", "image/jpeg", "image/webp"}
TAMANHO_MAXIMO = 10 * 1024 * 1024
# Abaixo disto e logo ou assinatura dentro do e-mail, nao comprovante.
TAMANHO_MINIMO_DE_IMAGEM = 15 * 1024
LADO_MAXIMO = 1568


class ArquivoInvalido(Exception):
    pass


def preparar_arquivo(conteudo, tipo):
    """PDF passa como veio; imagem vira JPEG de lado maior ate 1568 px.

    1568 e o tamanho em que o modelo le a imagem sem reduzir de novo: maior
    so custaria tokens. A mesma versao reduzida e a que fica guardada.
    """
    if tipo == "application/pdf":
        return conteudo, tipo
    try:
        imagem = ImageOps.exif_transpose(Image.open(io.BytesIO(conteudo)))
    except Exception as erro:
        raise ArquivoInvalido("A imagem não abriu.") from erro
    imagem = imagem.convert("RGB")
    imagem.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
    saida = io.BytesIO()
    imagem.save(saida, format="JPEG", quality=80)
    return saida.getvalue(), "image/jpeg"
