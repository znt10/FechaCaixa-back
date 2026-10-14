"""A entrada que nenhuma regra pegou entra como venda, sozinha.

Numa loja, quase todo dinheiro que entra e venda: o Pix de cada cliente vem com
um nome diferente, e classificar um por um nao tem fim. O que foge disso — a
transferencia entre lojas, o deposito da loterica, o cofrinho do PicPay — tem
caminho proprio, e a venda automatica nao pode atropelar nenhum deles.
"""

from django.test import TestCase

from app.models import CategoriaDeMovimento, Conta, TipoDeMovimento, TransacaoBancaria
from app.services.extratos import classificar, garantir_categorias_de_movimento, importar_extrato

from .fabricas import conta_padrao, criar_loja
from .test_extrato_bancario import criar_conta_bancaria, ofx
from .test_extrato_csv_picpay import csv_picpay

RECEBIMENTO = TipoDeMovimento.RECEBIMENTO
TRANSFERENCIA = TipoDeMovimento.TRANSFERENCIA


class EntradasComoVendaTests(TestCase):
    def setUp(self):
        self.conta = conta_padrao()
        self.loja = criar_loja(nome_loja="Loja A", cidade="Patos", endereco="Rua 1")
        self.banco = criar_conta_bancaria(self.loja)
        garantir_categorias_de_movimento(self.conta)
        self.vendas = CategoriaDeMovimento.objects.get(conta=self.conta, vendas=True)
        self.deposito = CategoriaDeMovimento.objects.get(conta=self.conta, nome="Depósito de dinheiro")

    def importar(self, *transacoes):
        return importar_extrato(self.banco, "x.ofx", ofx(transacoes))

    def test_entrada_sem_regra_vira_venda_automatica_e_saida_espera(self):
        self.importar(("20261001", "35.00", "PIX RECEBIDO MARIA"), ("20261001", "-80.00", "PIX ENVIADO JOAO"))

        entrada = TransacaoBancaria.objects.get(valor__gt=0)
        saida = TransacaoBancaria.objects.get(valor__lt=0)
        self.assertEqual((entrada.tipo, entrada.categoria, entrada.automatica), (RECEBIMENTO, self.vendas, True))
        self.assertIsNone(saida.tipo)

    def test_regra_aprendida_vence_a_venda(self):
        self.importar(("20261001", "500.00", "DEPOSITO DINHEIRO LOTERICA"))
        classificar(TransacaoBancaria.objects.get(), RECEBIMENTO, categoria=self.deposito)

        self.importar(("20261008", "700.00", "DEPOSITO DINHEIRO LOTERICA"))

        nova = TransacaoBancaria.objects.get(valor=700)
        self.assertEqual((nova.categoria, nova.automatica), (self.deposito, False))

    def test_aplicar_as_iguais_corrige_as_vendas_automaticas(self):
        """O primeiro deposito da loterica aparece ja como venda: marcar um
        como deposito com "aplicar as iguais" tem que levar os outros juntos."""
        self.importar(
            ("20261001", "500.00", "DEPOSITO DINHEIRO LOTERICA"),
            ("20261008", "700.00", "DEPOSITO DINHEIRO LOTERICA"),
        )
        primeiro, segundo = TransacaoBancaria.objects.order_by("data")

        iguais = classificar(primeiro, RECEBIMENTO, categoria=self.deposito, aplicar_as_iguais=True)

        segundo.refresh_from_db()
        self.assertEqual(iguais, 1)
        self.assertEqual((segundo.categoria, segundo.automatica), (self.deposito, False))

    def test_classificar_na_mao_tira_a_marca_de_automatica(self):
        self.importar(("20261001", "35.00", "PIX RECEBIDO MARIA"))
        transacao = TransacaoBancaria.objects.get()

        classificar(transacao, RECEBIMENTO, categoria=self.vendas)

        transacao.refresh_from_db()
        self.assertFalse(transacao.automatica)

    def test_cofrinho_do_picpay_nao_vira_venda(self):
        importar_extrato(self.banco, "picpay.csv", csv_picpay(
            '2026-10-12,10:00,"Dinheiro resgatado","Cofrinho","+R$ 200,00",',
            '2026-10-12,09:00,"Pix recebido","CLIENTE","+R$ 10,00",',
        ))

        resgate = TransacaoBancaria.objects.get(valor=200)
        self.assertIsNone(resgate.tipo)
        self.assertEqual(TransacaoBancaria.objects.get(valor=10).categoria, self.vendas)

    def test_com_vendas_desativada_a_entrada_espera(self):
        self.vendas.ativo = False
        self.vendas.save()

        self.importar(("20261001", "35.00", "PIX RECEBIDO MARIA"))

        self.assertIsNone(TransacaoBancaria.objects.get().tipo)

    def test_empresa_nova_ja_vem_com_deposito_e_vendas_marcada(self):
        outra = Conta.objects.create(nome="Outra Empresa")
        garantir_categorias_de_movimento(outra)
        self.assertTrue(CategoriaDeMovimento.objects.filter(conta=outra, vendas=True).exists())
        self.assertTrue(
            CategoriaDeMovimento.objects.filter(conta=outra, tipo=RECEBIMENTO, nome="Depósito de dinheiro").exists()
        )


class VendaAutomaticaETransferenciaTests(TestCase):
    """A Loja A recebe 500 da Loja B, e os extratos chegam em dias diferentes."""

    def setUp(self):
        self.conta = conta_padrao()
        self.loja_a = criar_loja(nome_loja="Loja A", cidade="Patos", endereco="Rua 1")
        self.loja_b = criar_loja(nome_loja="Loja B", cidade="Patos", endereco="Rua 2")
        self.banco_a = criar_conta_bancaria(self.loja_a, numero="11111-1")
        self.banco_b = criar_conta_bancaria(self.loja_b, numero="22222-2", banco="INTER")

    def importar_a(self, *transacoes):
        return importar_extrato(self.banco_a, "a.ofx", ofx(transacoes, conta="11111-1"))

    def importar_b(self, *transacoes):
        return importar_extrato(self.banco_b, "b.ofx", ofx(transacoes, conta="22222-2"))

    def test_venda_automatica_vira_entre_lojas_quando_chega_a_outra_ponta(self):
        self.importar_a(("20261002", "500.00", "PIX RECEBIDO LOJA B"))
        self.assertTrue(TransacaoBancaria.objects.get(conta_bancaria=self.banco_a).automatica)

        self.importar_b(("20261002", "-500.00", "PIX ENVIADO LOJA A"))

        entrada = TransacaoBancaria.objects.get(conta_bancaria=self.banco_a)
        self.assertEqual(entrada.tipo, TRANSFERENCIA)
        self.assertTrue(entrada.categoria.entre_lojas)
        self.assertFalse(entrada.automatica)
        self.assertIsNotNone(entrada.par_id)

    def test_entrada_que_pode_ser_transferencia_fica_para_a_gerente(self):
        """Duas entradas de 500 e uma saida de 500 na outra loja: uma delas e
        a transferencia. Virar venda as duas inflaria a venda em 500."""
        self.importar_b(("20261002", "-500.00", "PIX ENVIADO"))
        self.importar_a(("20261002", "500.00", "PIX RECEBIDO X"), ("20261002", "500.00", "PIX RECEBIDO Y"))

        entradas = TransacaoBancaria.objects.filter(conta_bancaria=self.banco_a)
        self.assertEqual([e.tipo for e in entradas], [None, None])
