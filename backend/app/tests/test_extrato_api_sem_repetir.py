"""A mesma transacao pela API e pelo arquivo, em qualquer ordem: uma linha so.

Os cenarios sao os do Nubank: o OFX descreve o Pix com o CPF mascarado e a
conta de destino; a API manda a descricao curta e o documento inteiro.
"""

import datetime
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from app.models import (
    CategoriaDeMovimento,
    ImportacaoDeExtrato,
    TipoDeMovimento,
    TransacaoBancaria,
)
from app.services.extratos import (
    RecusaDeExtrato,
    classificar,
    garantir_categorias_de_movimento,
    gravar_linhas_da_api,
    importar_extrato,
)
from app.services.extratos.api import mesma_contraparte
from app.services.extratos.linhas import LinhaDoExtrato

from .fabricas import conta_padrao, criar_loja
from .test_extrato_bancario import criar_conta_bancaria, ofx

TRANSFERENCIA = TipoDeMovimento.TRANSFERENCIA
RECEBIMENTO = TipoDeMovimento.RECEBIMENTO

DIA = datetime.date(2026, 10, 5)
DIA_SEGUINTE = DIA + datetime.timedelta(days=1)
NO_OFX = "20261005"

ANA = ("Ana Beatriz Souza", "12374533800", "745.338")
CARLOS = ("Carlos Lima", "98732165400", "321.654")


def pix_no_ofx(pessoa):
    """A descricao do Pix enviado como o OFX do Nubank escreve."""
    nome, _, meio_do_cpf = pessoa
    return (
        f"Transferência enviada pelo Pix - {nome} - •••.{meio_do_cpf}-•• - "
        f"PICPAY (0380) Agência: 1 Conta: 106539100-5"
    )


def pix_da_api(id_externo, valor, pessoa=ANA, data=DIA):
    """A mesma transacao como a API entrega (ver pluggy.transacoes)."""
    nome, documento, _ = pessoa
    return LinhaDoExtrato(
        data=data,
        valor=Decimal(valor),
        descricao=f"Transferência enviada pelo Pix - {nome}",
        id_externo=id_externo,
        contraparte_nome=nome,
        contraparte_documento=documento,
        contraparte_conta="1065391005",
    )


class ApiSemRepetirTests(TestCase):
    def setUp(self):
        self.conta = conta_padrao()
        self.loja = criar_loja(nome_loja="Loja A", cidade="Patos", endereco="Rua 1")
        self.banco = criar_conta_bancaria(self.loja, banco="NUBANK")
        garantir_categorias_de_movimento(self.conta)
        self.retirada = CategoriaDeMovimento.objects.get(nome="Retirada de sócio")
        self.vendas = CategoriaDeMovimento.objects.get(vendas=True)

    def gravar(self, *linhas, banco=None):
        return gravar_linhas_da_api(banco or self.banco, list(linhas), '{"results": []}')

    def importar(self, *transacoes, banco=None):
        banco = banco or self.banco
        return importar_extrato(banco, "nubank.ofx", ofx(transacoes, conta=banco.numero))

    def linhas(self, banco=None):
        return TransacaoBancaria.objects.filter(conta_bancaria=banco or self.banco)

    def test_api_duas_vezes_nao_duplica(self):
        linhas = [pix_da_api("tx-1", "-10.00"), pix_da_api("tx-2", "25.00", CARLOS)]

        primeira = self.gravar(*linhas)
        segunda = self.gravar(*linhas)

        self.assertEqual((primeira.novas, primeira.adotadas), (2, 0))
        self.assertEqual((segunda.novas, segunda.repetidas, segunda.adotadas), (0, 2, 0))
        self.assertEqual(self.linhas().count(), 2)
        saida = self.linhas().get(id_externo="tx-1")
        entrada = self.linhas().get(id_externo="tx-2")
        self.assertEqual((saida.tipo, saida.categoria), (None, None))
        # Entrada da API e como a do arquivo: vira venda sozinha.
        self.assertEqual(
            (entrada.tipo, entrada.categoria, entrada.automatica), (RECEBIMENTO, self.vendas, True)
        )
        self.assertEqual(saida.chave, "pluggy:tx-1")
        self.assertEqual(
            (saida.contraparte_nome, saida.contraparte_documento, saida.contraparte_conta),
            ("Ana Beatriz Souza", "12374533800", "1065391005"),
        )

    def test_ofx_depois_da_api_adota_e_conta_como_repetida(self):
        self.gravar(pix_da_api("tx-1", "-10.00"))

        resultado = self.importar((NO_OFX, "-10.00", pix_no_ofx(ANA)))

        self.assertEqual((resultado.novas, resultado.repetidas), (0, 1))
        self.assertEqual(self.linhas().count(), 1)
        transacao = self.linhas().get()
        self.assertEqual(transacao.id_externo, "tx-1")
        self.assertIsNotNone(transacao.chave_do_arquivo)
        # Adotar nao regrava: a descricao continua a que chegou primeiro.
        self.assertEqual(
            transacao.descricao, "Transferência enviada pelo Pix - Ana Beatriz Souza"
        )
        self.assertEqual((transacao.tipo, transacao.categoria), (None, None))

    def test_api_depois_do_ofx_adota_e_mantem_a_classificacao(self):
        self.importar((NO_OFX, "-10.00", pix_no_ofx(ANA)))
        transacao = self.linhas().get()
        descricao_do_ofx = transacao.descricao
        classificar(transacao, TRANSFERENCIA, categoria=self.retirada)

        resultado = self.gravar(pix_da_api("tx-1", "-10.00"))

        self.assertEqual((resultado.novas, resultado.adotadas), (0, 1))
        self.assertEqual(self.linhas().count(), 1)
        transacao.refresh_from_db()
        self.assertEqual(
            (transacao.tipo, transacao.categoria, transacao.descricao),
            (TRANSFERENCIA, self.retirada, descricao_do_ofx),
        )
        self.assertEqual(transacao.id_externo, "tx-1")
        self.assertEqual(
            (
                transacao.contraparte_nome,
                transacao.contraparte_documento,
                transacao.contraparte_conta,
            ),
            ("Ana Beatriz Souza", "12374533800", "1065391005"),
        )

    def test_o_mesmo_ofx_duas_vezes_depois_da_api_nao_duplica(self):
        self.gravar(pix_da_api("tx-1", "-10.00"), pix_da_api("tx-2", "-40.00", CARLOS))
        arquivo = [(NO_OFX, "-10.00", pix_no_ofx(ANA)), (NO_OFX, "-40.00", pix_no_ofx(CARLOS))]

        primeira = self.importar(*arquivo)
        segunda = self.importar(*arquivo)

        self.assertEqual((primeira.novas, primeira.repetidas), (0, 2))
        self.assertEqual((segunda.novas, segunda.repetidas), (0, 2))
        self.assertEqual(self.linhas().count(), 2)
        self.assertEqual(
            set(self.linhas().values_list("tipo", "categoria")), {(None, None)}
        )

    def test_dois_pix_iguais_no_mesmo_dia_ficam_dois_nas_duas_ordens(self):
        outro_banco = criar_conta_bancaria(self.loja, numero="77777-7", banco="NUBANK")
        dois_da_api = [pix_da_api("tx-1", "-10.00"), pix_da_api("tx-2", "-10.00")]
        dois_no_ofx = [(NO_OFX, "-10.00", pix_no_ofx(ANA))] * 2

        # API e depois arquivo, num banco; arquivo e depois API, no outro.
        self.gravar(*dois_da_api)
        pelo_arquivo = self.importar(*dois_no_ofx)
        self.importar(*dois_no_ofx, banco=outro_banco)
        pela_api = self.gravar(*dois_da_api, banco=outro_banco)

        self.assertEqual((pelo_arquivo.novas, pelo_arquivo.repetidas), (0, 2))
        self.assertEqual((pela_api.novas, pela_api.adotadas), (0, 2))
        # E repetir as duas fontes nao muda nada.
        for banco in (self.banco, outro_banco):
            self.gravar(*dois_da_api, banco=banco)
            self.importar(*dois_no_ofx, banco=banco)
            linhas = self.linhas(banco)
            self.assertEqual(linhas.count(), 2)
            self.assertEqual(set(linhas.values_list("id_externo", flat=True)), {"tx-1", "tx-2"})
            self.assertEqual(set(linhas.values_list("tipo", "categoria")), {(None, None)})

    def test_mesmo_valor_no_dia_para_pessoas_diferentes_casa_pela_contraparte(self):
        self.importar(
            (NO_OFX, "-10.00", pix_no_ofx(ANA)),
            (NO_OFX, "-10.00", pix_no_ofx(CARLOS)),
        )
        ana = self.linhas().get(descricao__contains="Ana Beatriz")
        carlos = self.linhas().get(descricao__contains="Carlos Lima")

        self.gravar(pix_da_api("tx-carlos", "-10.00", CARLOS))

        ana.refresh_from_db()
        carlos.refresh_from_db()
        # A da Ana tem o id menor e seria a primeira: casou a do Carlos.
        self.assertEqual((ana.id_externo, carlos.id_externo), (None, "tx-carlos"))
        self.assertEqual(self.linhas().count(), 2)
        self.assertEqual((carlos.tipo, carlos.categoria), (None, None))

        # Na outra ordem, o arquivo tambem casa pela contraparte.
        outro_banco = criar_conta_bancaria(self.loja, numero="77777-7", banco="NUBANK")
        self.gravar(
            pix_da_api("tx-ana", "-10.00"),
            pix_da_api("tx-carlos", "-10.00", CARLOS),
            banco=outro_banco,
        )

        self.importar((NO_OFX, "-10.00", pix_no_ofx(CARLOS)), banco=outro_banco)

        linhas = self.linhas(outro_banco)
        self.assertEqual(linhas.count(), 2)
        self.assertIsNone(linhas.get(id_externo="tx-ana").chave_do_arquivo)
        self.assertIsNotNone(linhas.get(id_externo="tx-carlos").chave_do_arquivo)

    def test_api_casa_pela_contraparte_mesmo_com_outra_linha_antes(self):
        """O OFX so tem o Pix da Ana; a API traz o do Carlos primeiro.

        A Pluggy manda o mais novo primeiro. Se o Carlos, por vir antes e nao
        reconhecer ninguem, levasse a linha da Ana, o extrato mostraria a Ana
        duas vezes e o Carlos nenhuma.
        """
        self.importar((NO_OFX, "-10.00", pix_no_ofx(ANA)))
        do_ofx = self.linhas().get()

        resultado = self.gravar(
            pix_da_api("tx-carlos", "-10.00", CARLOS), pix_da_api("tx-ana", "-10.00", ANA)
        )

        self.assertEqual((resultado.novas, resultado.adotadas), (1, 1))
        self.assertEqual(self.linhas().count(), 2)
        do_ofx.refresh_from_db()
        self.assertEqual(do_ofx.id_externo, "tx-ana")
        self.assertEqual(self.linhas().get(id_externo="tx-carlos").chave, "pluggy:tx-carlos")

    def test_arquivo_casa_pela_contraparte_mesmo_com_outra_linha_antes(self):
        """O espelho: a API buscou antes do Pix do Carlos; o OFX traz os dois."""
        self.gravar(pix_da_api("tx-ana", "-10.00", ANA))

        resultado = self.importar(
            (NO_OFX, "-10.00", pix_no_ofx(CARLOS)), (NO_OFX, "-10.00", pix_no_ofx(ANA))
        )

        self.assertEqual((resultado.novas, resultado.repetidas), (1, 1))
        self.assertEqual(self.linhas().count(), 2)
        # O Carlos entrou como linha nova do arquivo, e nao sumiu numa da Ana.
        self.assertEqual(self.linhas().filter(descricao__contains="Carlos Lima").count(), 1)
        da_api = self.linhas().get(id_externo="tx-ana")
        do_carlos = self.linhas().get(descricao__contains="Carlos Lima")
        self.assertIsNotNone(da_api.chave_do_arquivo)
        self.assertNotEqual(da_api.chave_do_arquivo, do_carlos.chave)
        self.assertIsNone(do_carlos.id_externo)

    def test_dia_diferente_nao_casa(self):
        self.importar((NO_OFX, "-10.00", pix_no_ofx(ANA)))

        resultado = self.gravar(pix_da_api("tx-1", "-10.00", data=DIA_SEGUINTE))

        self.assertEqual((resultado.novas, resultado.adotadas), (1, 0))
        self.assertEqual(self.linhas().count(), 2)
        self.assertIsNone(self.linhas().get(data=DIA).id_externo)
        nova = self.linhas().get(id_externo="tx-1")
        self.assertEqual((nova.tipo, nova.categoria), (None, None))

    def test_sem_linha_nova_nao_cria_importacao(self):
        com_nova = self.gravar(pix_da_api("tx-1", "-10.00"))
        self.assertRegex(
            com_nova.importacao.nome_do_arquivo, r"^Pluggy \d{2}/\d{2}/\d{4} \d{2}:\d{2}$"
        )
        self.assertEqual(com_nova.importacao.conteudo_bruto, '{"results": []}')
        self.importar((NO_OFX, "-20.00", pix_no_ofx(CARLOS)))
        importacoes = ImportacaoDeExtrato.objects.count()

        repetida = self.gravar(pix_da_api("tx-1", "-10.00"))
        adotada = self.gravar(pix_da_api("tx-2", "-20.00", CARLOS))

        self.assertEqual((repetida.novas, adotada.novas, adotada.adotadas), (0, 0, 1))
        self.assertIsNone(repetida.importacao)
        self.assertIsNone(adotada.importacao)
        self.assertEqual(ImportacaoDeExtrato.objects.count(), importacoes)
        self.assertEqual(self.linhas().count(), 2)

    def test_conta_desativada_recusa(self):
        self.banco.ativo = False
        self.banco.save()

        with self.assertRaises(RecusaDeExtrato):
            self.gravar(pix_da_api("tx-1", "-10.00"))

        self.assertEqual(self.linhas().count(), 0)
        self.assertFalse(ImportacaoDeExtrato.objects.exists())

    def test_linha_do_arquivo_que_a_api_adotou_nao_e_adotada_por_outra_do_arquivo(self):
        """Ela ja tem a impressao digital do arquivo dela, na `chave`.

        O primeiro OFX tinha um Pix; a API trouxe dois. O OFX seguinte traz
        tres: o segundo e da API, e o terceiro e novo. Se o segundo casasse com
        a linha que ja era do primeiro OFX, a linha da API ficaria livre e
        engoliria o terceiro — e um Pix de verdade sumiria do extrato ate a API
        busca-lo, ou para sempre, se a conexao cair antes.
        """
        self.importar((NO_OFX, "-10.00", pix_no_ofx(ANA)))
        self.gravar(pix_da_api("tx-1", "-10.00"), pix_da_api("tx-2", "-10.00"))

        resultado = self.importar(*[(NO_OFX, "-10.00", pix_no_ofx(ANA))] * 3)

        self.assertEqual((resultado.novas, resultado.repetidas), (1, 2))
        self.assertEqual(self.linhas().count(), 3)

    def test_a_mesma_transacao_duas_vezes_na_lista_entra_uma(self):
        """Paginas que se cruzam podem repetir a transacao na mesma busca."""
        resultado = self.gravar(pix_da_api("tx-1", "-10.00"), pix_da_api("tx-1", "-10.00"))

        self.assertEqual((resultado.novas, resultado.repetidas), (1, 1))
        self.assertEqual(self.linhas().count(), 1)

    def test_linha_adotada_passa_pela_classificacao(self):
        """A regra aprendida depois do OFX chega a linha quando a API a adota."""
        self.importar(
            (NO_OFX, "-10.00", pix_no_ofx(ANA)),
            ("20261006", "-10.00", pix_no_ofx(ANA)),
        )
        classificar(self.linhas().get(data=DIA_SEGUINTE), TRANSFERENCIA, categoria=self.retirada)

        resultado = self.gravar(pix_da_api("tx-1", "-10.00"))

        self.assertEqual((resultado.adotadas, resultado.classificadas), (1, 1))
        adotada = self.linhas().get(id_externo="tx-1")
        self.assertEqual((adotada.tipo, adotada.categoria), (TRANSFERENCIA, self.retirada))


class MesmaContraparteTests(SimpleTestCase):
    def test_cpf_mascarado_casa_pelos_digitos_do_meio(self):
        cpf = "12374533800"
        self.assertTrue(mesma_contraparte("Pix - Fulano - •••.745.338-•• - PICPAY", "", cpf))
        self.assertFalse(mesma_contraparte("Pix - Fulano - •••.745.339-•• - PICPAY", "", cpf))

    def test_cnpj_inteiro_casa(self):
        descricao = "Pagamento - Distribuidora X - 28.272.925/0001-00"
        self.assertTrue(mesma_contraparte(descricao, "", "28272925000100"))

    def test_pedaco_curto_de_numero_nao_basta(self):
        """Agencia, dia e mes cabem em qualquer documento."""
        self.assertFalse(mesma_contraparte("Pix 05/10 - Agência: 0380", "", "12374533800"))

    def test_nome_casa_com_duas_palavras_ou_mais(self):
        descricao = "Transferência enviada pelo Pix - Ana Beatriz Souza - •••"
        self.assertTrue(mesma_contraparte(descricao, "ANA BEATRIZ SOUZA", ""))
        self.assertTrue(mesma_contraparte(descricao, "Ana Beatriz Souza", ""))
        self.assertFalse(mesma_contraparte(descricao, "Ana", ""))
        self.assertFalse(mesma_contraparte(descricao, "", ""))

    def test_nome_dentro_de_outro_nome_nao_casa(self):
        descricao = "Transferência enviada pelo Pix - Juliana Souza"
        self.assertFalse(mesma_contraparte(descricao, "Ana Souza", ""))
