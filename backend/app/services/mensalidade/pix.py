"""O Pix copia e cola da mensalidade, no padrao BR Code do Banco Central.

Pix estatico com valor: o mesmo que o app do banco gera para a chave do
dono, mas ja com o preco da empresa preenchido e um identificador dela.
Sem gateway nenhum no caminho.
"""

import unicodedata

import segno


def _campo(id_, valor):
    return f"{id_}{len(valor):02d}{valor}"


def _sem_acento(texto):
    return (
        unicodedata.normalize("NFKD", texto or "")
        .encode("ascii", "ignore")
        .decode("ascii")
    )


def crc16(texto):
    """CRC-16/CCITT-FALSE (polinomio 0x1021, inicio 0xFFFF), em 4 hex."""
    crc = 0xFFFF
    for byte in texto.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f"{crc:04X}"


def identificador_da_conta(public_id):
    """"FC" + 23 hex do public_id: so letras e numeros, ate 25, como o txid
    do Pix estatico exige. Ha banco que mostra isso no extrato de quem
    recebe, e ajuda o dono a saber de quem e o Pix."""
    return "FC" + public_id.hex.upper()[:23]


def copia_e_cola(*, chave, nome, cidade, valor, identificador):
    conta_pix = _campo("00", "br.gov.bcb.pix") + _campo("01", chave)
    corpo = (
        _campo("00", "01")
        + _campo("26", conta_pix)
        + _campo("52", "0000")
        + _campo("53", "986")
        + _campo("54", f"{valor:.2f}")
        + _campo("58", "BR")
        # Truncado e so depois sem espaco no fim: "... NETO JUNIOR" cortado
        # em 25 nao pode terminar num espaco que o banco rejeitaria.
        + _campo("59", _sem_acento(nome).upper()[:25].strip())
        + _campo("60", _sem_acento(cidade)[:15].strip())
        + _campo("62", _campo("05", identificador))
        + "6304"
    )
    return corpo + crc16(corpo)


def qr_svg(texto):
    """O QR do copia e cola, como data URI para um <img> da tela."""
    return segno.make(texto, error="m").svg_data_uri(scale=6, border=2)
