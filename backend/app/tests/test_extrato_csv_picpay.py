"""O extrato do PicPay em CSV, que e o que o app exporta (nao ha OFX).

Os dados daqui sao inventados no formato do arquivo real: UTF-8 com BOM,
virgula, campos entre aspas, a linha mais nova primeiro e o valor com o
"menos" tipografico (U+2212) na saida.
"""

import datetime
from decimal import Decimal

from django.test import TestCase

from app.models import TransacaoBancaria
from app.services.extratos import RecusaDeExtrato, importar_extrato
from app.services.extratos.importar import ler_arquivo

from .fabricas import criar_loja
from .test_extrato_bancario import criar_conta_bancaria

CABECALHO = 'data,hora,tipo,"origem / destino",valor,"forma de pagamento"'


def csv_picpay(*linhas):
    return ("﻿" + "\n".join([CABECALHO, *linhas]) + "\n").encode("utf-8")


ARQUIVO = csv_picpay(
    '2026-10-12,23:15,"Pix enviado","FULANA DE TESTE","−R$ 7,50","Com saldo"',
    '2026-10-12,23:13,"Pix recebido","12.345.678 CLIENTE INVENTADO","+R$ 8,00",',
    '2026-10-11,10:00,"Pagamento realizado","CONTA DE LUZ","−R$ 1.234,56","Com saldo + cartão"',
    '2026-10-10,09:30,"Pix recebido","OUTRO CLIENTE","+R$ 2.500,00",',
)


class LeitorDoCsvDoPicpayTests(TestCase):
    def test_le_data_valor_com_sinal_e_descricao(self):
        extrato = ler_arquivo("extrato-2026-10-10-2026-10-12.csv", ARQUIVO)

        self.assertEqual(len(extrato.linhas), 4)
        primeira, segunda, terceira, quarta = extrato.linhas
        self.assertEqual(primeira.data, datetime.date(2026, 10, 12))
        self.assertEqual(primeira.valor, Decimal("-7.50"))
        self.assertEqual(primeira.descricao, "Pix enviado FULANA DE TESTE")
        self.assertEqual(segunda.valor, Decimal("8.00"))
        self.assertEqual(terceira.valor, Decimal("-1234.56"))
        self.assertEqual(quarta.valor, Decimal("2500.00"))

    def test_periodo_vai_da_linha_mais_antiga_a_mais_nova(self):
        extrato = ler_arquivo("x.csv", ARQUIVO)
        self.assertEqual(extrato.periodo_de, datetime.date(2026, 10, 10))
        self.assertEqual(extrato.periodo_ate, datetime.date(2026, 10, 12))

    def test_reconhece_pelo_conteudo_mesmo_sem_o_nome_csv(self):
        self.assertEqual(len(ler_arquivo("download", ARQUIVO).linhas), 4)

    def test_recusa_o_arquivo_inteiro_se_uma_linha_nao_se_le(self):
        """Pular a linha em silencio seria dinheiro sumindo do extrato."""
        arquivo = csv_picpay(
            '2026-10-12,23:15,"Pix enviado","A","−R$ 7,50","Com saldo"',
            '2026-10-12,23:13,"Pix recebido","B","oito reais",',
        )
        with self.assertRaises(RecusaDeExtrato) as erro:
            ler_arquivo("x.csv", arquivo)
        self.assertIn("linha 3", erro.exception.mensagem)

    def test_csv_que_nao_e_do_picpay_continua_recusado(self):
        with self.assertRaises(RecusaDeExtrato):
            ler_arquivo("outro.csv", b"data,valor\n2026-10-01,10\n")


class ImportacaoDoCsvDoPicpayTests(TestCase):
    """O arquivo de cada semana repete o comeco do mes: nao pode duplicar."""

    def setUp(self):
        self.loja = criar_loja(nome_loja="Loja A", cidade="Patos", endereco="Rua 1")
        self.conta_bancaria = criar_conta_bancaria(self.loja)

    def test_importa_e_nao_duplica_na_segunda_vez(self):
        primeira = importar_extrato(self.conta_bancaria, "a.csv", ARQUIVO)
        segunda = importar_extrato(self.conta_bancaria, "a.csv", ARQUIVO)

        self.assertEqual((primeira.novas, primeira.repetidas), (4, 0))
        self.assertEqual((segunda.novas, segunda.repetidas), (0, 4))
        self.assertEqual(TransacaoBancaria.objects.count(), 4)
