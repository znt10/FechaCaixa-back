import uuid
from decimal import Decimal

from django.test import SimpleTestCase

from app.services.mensalidade.pix import (
    copia_e_cola,
    crc16,
    identificador_da_conta,
    qr_svg,
)


def campos(texto):
    """Le o TLV do BR Code: ID de 2, tamanho de 2, valor."""
    lidos, i = {}, 0
    while i < len(texto):
        id_, tamanho = texto[i:i + 2], int(texto[i + 2:i + 4])
        lidos[id_] = texto[i + 4:i + 4 + tamanho]
        i += 4 + tamanho
    return lidos


def gerar(**mudanca):
    dados = {
        "chave": "+5511999998888",
        "nome": "Maria da Silva",
        "cidade": "Patos",
        "valor": Decimal("100.00"),
        "identificador": "FC123",
    }
    dados.update(mudanca)
    return copia_e_cola(**dados)


class Crc16Tests(SimpleTestCase):
    def test_valor_de_verificacao_padrao(self):
        # CRC-16/CCITT-FALSE de "123456789" e 0x29B1 em toda implementacao.
        self.assertEqual(crc16("123456789"), "29B1")


class CopiaEColaTests(SimpleTestCase):
    def test_campos_no_lugar_certo(self):
        lido = campos(gerar())

        self.assertEqual(lido["00"], "01")
        self.assertEqual(campos(lido["26"]), {"00": "br.gov.bcb.pix", "01": "+5511999998888"})
        self.assertEqual(lido["52"], "0000")
        self.assertEqual(lido["53"], "986")
        self.assertEqual(lido["54"], "100.00")
        self.assertEqual(lido["58"], "BR")
        self.assertEqual(lido["59"], "MARIA DA SILVA")
        self.assertEqual(lido["60"], "Patos")
        self.assertEqual(campos(lido["62"]), {"05": "FC123"})

    def test_termina_com_o_crc_do_que_vem_antes(self):
        texto = gerar()
        self.assertTrue(texto[:-4].endswith("6304"))
        self.assertEqual(texto[-4:], crc16(texto[:-4]))

    def test_centavos(self):
        self.assertEqual(campos(gerar(valor=Decimal("0.01")))["54"], "0.01")
        self.assertEqual(campos(gerar(valor=Decimal("99.9")))["54"], "99.90")

    def test_nome_e_cidade_com_acento_e_compridos(self):
        lido = campos(gerar(
            nome="José Cícero da Silva Neto Júnior",
            cidade="São José do Rio Preto",
        ))
        self.assertEqual(lido["59"], "JOSE CICERO DA SILVA NETO")
        self.assertEqual(lido["60"], "Sao Jose do Rio")


class IdentificadorTests(SimpleTestCase):
    def test_so_letras_e_numeros_ate_25(self):
        identificador = identificador_da_conta(
            uuid.UUID("12345678-1234-5678-1234-567812345678")
        )
        self.assertEqual(identificador, "FC12345678123456781234567")
        self.assertLessEqual(len(identificador), 25)
        self.assertTrue(identificador.isalnum())


class QrTests(SimpleTestCase):
    def test_devolve_um_svg_pronto_para_img(self):
        self.assertTrue(qr_svg(gerar()).startswith("data:image/svg+xml"))
