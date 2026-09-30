"""O extrato ja lido, independente de onde veio.

Todo leitor (OFX hoje; CSV do PicPay e API do banco depois) entrega a mesma
coisa: uma lista de LinhaDoExtrato. Daqui para a frente ninguem sabe o formato
do arquivo, e e isso que deixa a fonte trocar sem mexer na regra de repeticao.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


class RecusaDeExtrato(Exception):
    """O arquivo nao pode entrar. A mensagem vai para a tela."""

    def __init__(self, mensagem):
        self.mensagem = mensagem
        super().__init__(mensagem)


@dataclass(frozen=True)
class LinhaDoExtrato:
    data: date
    # Com sinal: negativo e saida.
    valor: Decimal
    descricao: str
    id_do_banco: str = ""


@dataclass
class ExtratoLido:
    linhas: list = field(default_factory=list)
    # O numero da conta como o banco escreveu no arquivo, quando escreveu.
    # Serve so para recusar o extrato subido na conta errada.
    conta_no_arquivo: str = ""
    periodo_de: date | None = None
    periodo_ate: date | None = None


def normalizar_descricao(texto):
    """Maiusculas, sem acento, espacos colapsados.

    E a forma que entra na chave de repeticao: o mesmo banco ja mandou
    "Pix enviado" num arquivo e "PIX ENVIADO" no outro, e com acento ou sem
    conforme a codificacao do arquivo.
    """
    sem_acento = (
        unicodedata.normalize("NFKD", texto or "")
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return " ".join(sem_acento.upper().split())


def assinatura_da_descricao(texto):
    """A descricao sem numeros e pontuacao, para a regra aprendida.

    "PIX ENVIADO 29/09 12345 DISTRIBUIDORA X" e "PIX ENVIADO 06/10 67890
    DISTRIBUIDORA X" sao o mesmo fornecedor: com data e documento no meio,
    cada linha seria uma regra diferente e nada seria aprendido.
    """
    so_letras = re.sub(r"[^A-Z ]+", " ", normalizar_descricao(texto))
    return " ".join(so_letras.split())[:255]
