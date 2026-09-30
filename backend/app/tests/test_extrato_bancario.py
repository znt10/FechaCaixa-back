import datetime
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from app.models import (
    CategoriaDeMovimento,
    Conta,
    ContaBancaria,
    ElementoDeDespesa,
    RegraDeClassificacao,
    TipoDeMovimento,
    TransacaoBancaria,
)
from app.services.extratos import (
    ClassificacaoInvalida,
    RecusaDeExtrato,
    classificar,
    garantir_categorias_de_movimento,
    importar_extrato,
)
from app.services.extratos.importar import chaves_das_linhas
from app.services.extratos.linhas import LinhaDoExtrato, assinatura_da_descricao
from app.services.extratos.ofx import ler_ofx
from app.services.plano_de_contas import garantir_plano_de_contas

from .fabricas import conta_padrao, criar_loja, vincular_conta

FIXTURES_OFX = Path(__file__).resolve().parent / "fixtures" / "ofx"

PAGAMENTO = TipoDeMovimento.PAGAMENTO
TRANSFERENCIA = TipoDeMovimento.TRANSFERENCIA
RECEBIMENTO = TipoDeMovimento.RECEBIMENTO


def ofx(transacoes, conta="12345-6"):
    """Um OFX SGML minimo. transacoes: (data "AAAAMMDD", valor, memo[, fitid])."""
    blocos = []
    for numero, transacao in enumerate(transacoes, start=1):
        data, valor, memo, *resto = transacao
        fitid = resto[0] if resto else f"F{numero}"
        blocos.append(
            f"<STMTTRN>\n<TRNTYPE>OTHER\n<DTPOSTED>{data}\n<TRNAMT>{valor}\n"
            f"<FITID>{fitid}\n<MEMO>{memo}\n</STMTTRN>\n"
        )
    return (
        "OFXHEADER:100\nDATA:OFXSGML\n\n<OFX>\n<BANKMSGSRSV1><STMTTRNRS><STMTRS>\n"
        f"<BANKACCTFROM>\n<BANKID>033\n<ACCTID>{conta}\n</BANKACCTFROM>\n"
        "<BANKTRANLIST>\n" + "".join(blocos) + "</BANKTRANLIST>\n"
        "</STMTRS></STMTTRNRS></BANKMSGSRSV1>\n</OFX>\n"
    ).encode("utf-8")


def criar_conta_bancaria(loja, numero="12345-6", banco="SANTANDER"):
    return ContaBancaria.objects.create(loja=loja, banco=banco, agencia="3301", numero=numero)


class LeitorDeOfxTests(TestCase):
    def test_le_o_sgml_do_santander_em_1252_sem_fechamento(self):
        extrato = ler_ofx((FIXTURES_OFX / "santander_sgml_1252.ofx").read_bytes())

        self.assertEqual(len(extrato.linhas), 3)
        primeira, segunda, terceira = extrato.linhas
        self.assertEqual(primeira.data, datetime.date(2026, 10, 1))
        self.assertEqual(primeira.valor, Decimal("1234.56"))
        self.assertEqual(primeira.descricao, "PIX RECEBIDO JOÃO DA SILVA")
        # A segunda nao tem </STMTTRN>: termina onde a terceira comeca.
        self.assertEqual(segunda.valor, Decimal("-500.00"))
        self.assertEqual(segunda.descricao, "PIX ENVIADO LOJA B COMÉRCIO & CIA")
        self.assertEqual(terceira.descricao, "ENERGISA - PAGAMENTO DE BOLETO")
        self.assertEqual(terceira.id_do_banco, "20261003001")
        self.assertEqual(extrato.conta_no_arquivo, "3301130012345")
        self.assertEqual(extrato.periodo_de, datetime.date(2026, 10, 1))
        self.assertEqual(extrato.periodo_ate, datetime.date(2026, 10, 11))

    def test_le_o_ofx_2_em_xml(self):
        extrato = ler_ofx((FIXTURES_OFX / "inter_v2.ofx").read_bytes())

        self.assertEqual(
            [(l.data, l.valor) for l in extrato.linhas],
            [
                (datetime.date(2026, 10, 2), Decimal("500.00")),
                (datetime.date(2026, 10, 4), Decimal("-500.00")),
            ],
        )
        self.assertEqual(extrato.linhas[0].descricao, "Pix recebido - Loja A Comércio")
        self.assertEqual(extrato.conta_no_arquivo, "98765-4")

    def test_valor_com_milhar_nos_dois_formatos(self):
        extrato = ler_ofx(ofx([("20261001", "-1.234,56", "A"), ("20261001", "-1,234.56", "B")]))
        self.assertEqual({l.valor for l in extrato.linhas}, {Decimal("-1234.56")})

    def test_extrato_sem_transacao_e_valido(self):
        """Semana sem movimento: o arquivo vem vazio, e isso nao e erro."""
        extrato = ler_ofx(ofx([]))
        self.assertEqual(extrato.linhas, [])

    def test_recusa_o_que_nao_e_ofx(self):
        with self.assertRaises(RecusaDeExtrato) as erro:
            ler_ofx(b"data;valor;descricao\n01/10/2026;10,00;Pix\n")
        self.assertIn("OFX", erro.exception.mensagem)

    def test_recusa_fatura_de_cartao(self):
        texto = b"<OFX><CREDITCARDMSGSRSV1><CCSTMTRS><CCACCTFROM><ACCTID>1</CCACCTFROM></OFX>"
        with self.assertRaises(RecusaDeExtrato) as erro:
            ler_ofx(texto)
        self.assertIn("cartão de crédito", erro.exception.mensagem)

    def test_recusa_o_arquivo_inteiro_se_uma_linha_nao_se_le(self):
        """Pular a linha em silencio seria dinheiro sumindo do extrato."""
        with self.assertRaises(RecusaDeExtrato) as erro:
            ler_ofx(ofx([("20261001", "10.00", "A"), ("20261001", "dez reais", "B")]))
        self.assertIn("transação 2", erro.exception.mensagem)


class ChaveDeRepeticaoTests(TestCase):
    def linha(self, descricao="PIX FULANO", valor="-10.00", data=datetime.date(2026, 10, 5)):
        return LinhaDoExtrato(data=data, valor=Decimal(valor), descricao=descricao)

    def test_acento_caixa_e_espaco_nao_mudam_a_chave(self):
        a, = chaves_das_linhas([self.linha("Pix enviado  João")])
        b, = chaves_das_linhas([self.linha("PIX ENVIADO JOAO")])
        self.assertEqual(a, b)

    def test_linhas_iguais_no_mesmo_arquivo_ganham_numeros_diferentes(self):
        chaves = chaves_das_linhas([self.linha(), self.linha(), self.linha("OUTRA")])
        self.assertTrue(chaves[0].endswith("#1"))
        self.assertTrue(chaves[1].endswith("#2"))
        self.assertEqual(chaves[0][:-2], chaves[1][:-2])
        self.assertTrue(chaves[2].endswith("#1"))

    def test_assinatura_ignora_data_e_documento(self):
        self.assertEqual(
            assinatura_da_descricao("PIX ENVIADO 29/09 12345 Distribuidora X"),
            assinatura_da_descricao("PIX ENVIADO 06/10 67890 DISTRIBUIDORA X"),
        )


class ImportacaoSemRepetirTests(TestCase):
    """O caso do PicPay: o arquivo de cada sabado repete o comeco do mes."""

    def setUp(self):
        self.loja = criar_loja(nome_loja="Loja A", cidade="Patos", endereco="Rua 1")
        self.conta_bancaria = criar_conta_bancaria(self.loja)

    def importar(self, conteudo, nome="extrato.ofx"):
        return importar_extrato(self.conta_bancaria, nome, conteudo)

    def test_o_mesmo_arquivo_duas_vezes_nao_duplica(self):
        arquivo = ofx([("20261001", "-10.00", "A"), ("20261002", "20.00", "B")])

        primeira = self.importar(arquivo)
        segunda = self.importar(arquivo)

        self.assertEqual((primeira.novas, primeira.repetidas), (2, 0))
        self.assertEqual((segunda.novas, segunda.repetidas), (0, 2))
        self.assertEqual(TransacaoBancaria.objects.count(), 2)

    def test_periodo_sobreposto_so_acrescenta_o_que_e_novo(self):
        semana_1 = [("20261001", "-10.00", "A"), ("20261003", "30.00", "B")]
        semana_2 = [("20261006", "-40.00", "C"), ("20261010", "-5.00", "D")]

        self.importar(ofx(semana_1))
        resultado = self.importar(ofx(semana_1 + semana_2))

        self.assertEqual((resultado.novas, resultado.repetidas), (2, 2))
        self.assertEqual(
            sorted(TransacaoBancaria.objects.values_list("descricao", flat=True)),
            ["A", "B", "C", "D"],
        )

    def test_dois_pix_iguais_no_mesmo_dia_entram_os_dois(self):
        dois = [("20261005", "10.00", "PIX FULANO"), ("20261005", "10.00", "PIX FULANO")]

        self.assertEqual(self.importar(ofx(dois)).novas, 2)
        # O arquivo seguinte traz os mesmos dois: nada entra.
        self.assertEqual(self.importar(ofx(dois)).novas, 0)
        # Um terceiro igual no mesmo dia: so ele entra.
        tres = dois + [("20261005", "10.00", "PIX FULANO")]
        self.assertEqual(self.importar(ofx(tres)).novas, 1)
        self.assertEqual(TransacaoBancaria.objects.count(), 3)

    def test_fitid_novo_na_reexportacao_nao_duplica(self):
        """Ha banco que gera FITID novo a cada exportacao do mesmo periodo."""
        self.importar(ofx([("20261001", "-10.00", "A", "X1")]))
        resultado = self.importar(ofx([("20261001", "-10.00", "A", "OUTRO-ID")]))
        self.assertEqual(resultado.novas, 0)

    def test_reimportar_nao_desfaz_a_classificacao(self):
        conta = self.loja.conta
        garantir_plano_de_contas(conta)
        elemento = ElementoDeDespesa.objects.get(grupo__conta=conta, nome="Aluguel")
        arquivo = ofx([("20261001", "-500.00", "ALUGUEL")])
        self.importar(arquivo)
        transacao = TransacaoBancaria.objects.get()
        classificar(transacao, PAGAMENTO, elemento=elemento)

        self.importar(arquivo)

        transacao.refresh_from_db()
        self.assertEqual(transacao.elemento, elemento)

    def test_guarda_o_arquivo_como_ele_e(self):
        resultado = self.importar(ofx([("20261001", "-10.00", "PIX JOÃO")]))
        self.assertIn("PIX JOÃO", resultado.importacao.conteudo_bruto)

    def test_guarda_o_arquivo_e_o_periodo(self):
        resultado = self.importar(ofx([("20261001", "-10.00", "A"), ("20261004", "-1.00", "B")]))
        importacao = resultado.importacao
        self.assertIn("<OFX>", importacao.conteudo_bruto)
        self.assertEqual(importacao.periodo_de, datetime.date(2026, 10, 1))
        self.assertEqual(importacao.periodo_ate, datetime.date(2026, 10, 4))

    def test_recusa_extrato_de_outra_conta(self):
        with self.assertRaises(RecusaDeExtrato) as erro:
            self.importar(ofx([("20261001", "-10.00", "A")], conta="99999-9"))
        self.assertIn("Confira se escolheu a conta certa", erro.exception.mensagem)
        self.assertFalse(TransacaoBancaria.objects.exists())

    def test_aceita_o_numero_escrito_de_outro_jeito(self):
        """Com agencia na frente e zeros: o banco escreve como quer."""
        resultado = self.importar(ofx([("20261001", "-10.00", "A")], conta="3301000123456"))
        self.assertEqual(resultado.novas, 1)

    def test_recusa_conta_desativada(self):
        self.conta_bancaria.ativo = False
        self.conta_bancaria.save()
        with self.assertRaises(RecusaDeExtrato):
            self.importar(ofx([("20261001", "-10.00", "A")]))

    def test_recusa_formato_que_ainda_nao_le(self):
        with self.assertRaises(RecusaDeExtrato) as erro:
            self.importar(b"data,valor\n2026-10-01,10\n", nome="picpay.csv")
        self.assertIn("OFX", erro.exception.mensagem)


class PareamentoEntreLojasTests(TestCase):
    """A Loja A manda 500 para a Loja B pagar o aluguel."""

    def setUp(self):
        self.loja_a = criar_loja(nome_loja="Loja A", cidade="Patos", endereco="Rua 1")
        self.loja_b = criar_loja(nome_loja="Loja B", cidade="Patos", endereco="Rua 2")
        self.banco_a = criar_conta_bancaria(self.loja_a, numero="11111-1")
        self.banco_b = criar_conta_bancaria(self.loja_b, numero="22222-2", banco="INTER")

    def importar_a(self, *transacoes):
        return importar_extrato(self.banco_a, "a.ofx", ofx(transacoes, conta="11111-1"))

    def importar_b(self, *transacoes):
        return importar_extrato(self.banco_b, "b.ofx", ofx(transacoes, conta="22222-2"))

    def pontas(self):
        return (
            TransacaoBancaria.objects.get(conta_bancaria=self.banco_a),
            TransacaoBancaria.objects.get(conta_bancaria=self.banco_b),
        )

    def assertPareadas(self, saida, entrada):
        self.assertEqual(saida.par_id, entrada.id)
        self.assertEqual(entrada.par_id, saida.id)
        for ponta in (saida, entrada):
            self.assertEqual(ponta.tipo, TRANSFERENCIA)
            self.assertTrue(ponta.categoria.entre_lojas)

    def test_pareia_no_mesmo_dia(self):
        self.importar_a(("20261002", "-500.00", "PIX ENVIADO LOJA B"))
        resultado = self.importar_b(("20261002", "500.00", "PIX RECEBIDO LOJA A"))

        self.assertEqual(resultado.pareadas, 1)
        self.assertPareadas(*self.pontas())

    def test_pareia_com_um_dia_de_diferenca(self):
        self.importar_a(("20261002", "-500.00", "TED"))
        self.importar_b(("20261003", "500.00", "TED"))
        self.assertPareadas(*self.pontas())

    def test_nao_pareia_com_dois_dias_de_diferenca(self):
        self.importar_a(("20261002", "-500.00", "TED"))
        self.importar_b(("20261004", "500.00", "TED"))
        saida, entrada = self.pontas()
        self.assertIsNone(saida.par_id)
        self.assertIsNone(entrada.tipo)

    def test_na_duvida_nao_pareia(self):
        """Duas entradas de 500 no dia: uma pode ser venda. Escolher seria chutar."""
        self.importar_a(("20261002", "-500.00", "PIX ENVIADO LOJA B"))
        self.importar_b(
            ("20261002", "500.00", "PIX RECEBIDO LOJA A"),
            ("20261002", "500.00", "PIX RECEBIDO CLIENTE"),
        )
        self.assertFalse(TransacaoBancaria.objects.filter(par__isnull=False).exists())

    def test_saida_e_entrada_na_mesma_conta_nao_e_transferencia_entre_lojas(self):
        """Um estorno: saiu e voltou na mesma conta."""
        self.importar_a(("20261002", "-500.00", "PIX"), ("20261002", "500.00", "ESTORNO PIX"))
        self.assertFalse(TransacaoBancaria.objects.filter(par__isnull=False).exists())

    def test_nao_pareia_com_a_empresa_vizinha(self):
        vizinha = Conta.objects.create(nome="Vizinha")
        loja_c = criar_loja(conta=vizinha, nome_loja="Loja C", cidade="X", endereco="Y")
        banco_c = criar_conta_bancaria(loja_c, numero="33333-3")

        self.importar_a(("20261002", "-500.00", "PIX"))
        importar_extrato(banco_c, "c.ofx", ofx([("20261002", "500.00", "PIX")], conta="33333-3"))

        self.assertFalse(TransacaoBancaria.objects.filter(par__isnull=False).exists())

    def test_pareia_a_ponta_que_uma_regra_ja_marcou_como_entre_lojas(self):
        """A regra classifica a saida de A antes do extrato de B chegar; a
        entrada de B ainda precisa achar o par dela."""
        conta = self.loja_a.conta
        entre_lojas = garantir_categorias_de_movimento(conta)
        RegraDeClassificacao.objects.create(
            conta=conta,
            assinatura=assinatura_da_descricao("PIX ENVIADO LOJA B"),
            entrada=False,
            tipo=TRANSFERENCIA,
            categoria=entre_lojas,
        )

        self.importar_a(("20261002", "-500.00", "PIX ENVIADO LOJA B"))
        self.importar_b(("20261002", "500.00", "PIX RECEBIDO"))

        self.assertPareadas(*self.pontas())

    def test_reclassificar_uma_ponta_solta_as_duas(self):
        self.importar_a(("20261002", "-500.00", "PIX"))
        self.importar_b(("20261002", "500.00", "PIX"))
        saida, entrada = self.pontas()
        garantir_plano_de_contas(self.loja_a.conta)
        aluguel = ElementoDeDespesa.objects.get(nome="Aluguel")

        classificar(saida, PAGAMENTO, elemento=aluguel)

        saida.refresh_from_db()
        entrada.refresh_from_db()
        self.assertIsNone(saida.par_id)
        self.assertIsNone(entrada.par_id)
        # A entrada continua dizendo "entre lojas": quem decide o que ela e
        # agora e a gerente, nao o sistema.
        self.assertEqual(entrada.tipo, TRANSFERENCIA)


class ClassificacaoTests(TestCase):
    def setUp(self):
        self.conta = conta_padrao()
        self.loja = criar_loja(nome_loja="Loja A", cidade="Patos", endereco="Rua 1")
        self.banco = criar_conta_bancaria(self.loja)
        garantir_plano_de_contas(self.conta)
        garantir_categorias_de_movimento(self.conta)
        self.embalagem = ElementoDeDespesa.objects.get(nome="Embalagem")
        self.vendas = CategoriaDeMovimento.objects.get(nome="Vendas (Pix/maquininha)")
        self.retirada = CategoriaDeMovimento.objects.get(nome="Retirada de sócio")

    def importar(self, *transacoes):
        return importar_extrato(self.banco, "x.ofx", ofx(transacoes))

    def test_classificar_ensina_a_proxima_importacao(self):
        self.importar(("20261001", "-80.00", "PIX ENVIADO 01/10 DISTRIBUIDORA X"))
        classificar(TransacaoBancaria.objects.get(), PAGAMENTO, elemento=self.embalagem)

        resultado = self.importar(("20261008", "-95.00", "PIX ENVIADO 08/10 DISTRIBUIDORA X"))

        self.assertEqual(resultado.classificadas, 1)
        nova = TransacaoBancaria.objects.get(data=datetime.date(2026, 10, 8))
        self.assertEqual((nova.tipo, nova.elemento), (PAGAMENTO, self.embalagem))

    def test_regra_de_saida_nao_classifica_entrada(self):
        self.importar(("20261001", "-80.00", "PIX FULANO"))
        classificar(TransacaoBancaria.objects.get(), PAGAMENTO, elemento=self.embalagem)

        self.importar(("20261002", "80.00", "PIX FULANO"))

        self.assertIsNone(TransacaoBancaria.objects.get(valor__gt=0).tipo)

    def test_aplicar_as_iguais(self):
        self.importar(
            ("20261001", "25.00", "PIX RECEBIDO"),
            ("20261002", "30.00", "PIX RECEBIDO"),
            ("20261002", "-30.00", "PIX RECEBIDO"),
            ("20261003", "40.00", "OUTRA COISA"),
        )
        primeira = TransacaoBancaria.objects.get(valor=Decimal("25.00"))

        iguais = classificar(primeira, RECEBIMENTO, categoria=self.vendas, aplicar_as_iguais=True)

        self.assertEqual(iguais, 1)
        self.assertEqual(TransacaoBancaria.objects.filter(categoria=self.vendas).count(), 2)

    def test_limpar_a_classificacao_nao_apaga_a_regra(self):
        self.importar(("20261001", "-80.00", "SAQUE"))
        transacao = TransacaoBancaria.objects.get()
        classificar(transacao, TRANSFERENCIA, categoria=self.retirada)

        classificar(transacao, None)

        transacao.refresh_from_db()
        self.assertIsNone(transacao.tipo)
        self.assertTrue(RegraDeClassificacao.objects.filter(assinatura="SAQUE").exists())

    def test_recusa_combinacoes_que_o_relatorio_nao_soma(self):
        self.importar(("20261001", "-80.00", "X"))
        transacao = TransacaoBancaria.objects.get()
        casos = [
            (PAGAMENTO, None, None),
            (PAGAMENTO, self.embalagem, self.vendas),
            (RECEBIMENTO, None, None),
            (RECEBIMENTO, self.embalagem, None),
            (RECEBIMENTO, None, self.retirada),
            (None, self.embalagem, None),
        ]
        for tipo, elemento, categoria in casos:
            with self.subTest(tipo=tipo, elemento=elemento, categoria=categoria):
                with self.assertRaises(ClassificacaoInvalida):
                    classificar(transacao, tipo, elemento=elemento, categoria=categoria)


class CenarioDoBanco(TestCase):
    """Duas lojas da empresa com conta no banco, e uma vizinha. Sem testes
    proprios: as classes abaixo herdam so o cenario, e nao os testes umas das
    outras."""

    def setUp(self):
        for nome in ("Admin", "Gerente", "Funcionario"):
            Group.objects.get_or_create(name=nome)

        self.conta = conta_padrao()
        self.conta.modulo_banco_ativo = True
        self.conta.save()
        self.loja_a = criar_loja(nome_loja="Loja A", cidade="Patos", endereco="Rua 1")
        self.loja_b = criar_loja(nome_loja="Loja B", cidade="Patos", endereco="Rua 2")
        self.banco_a = criar_conta_bancaria(self.loja_a, numero="11111-1")
        self.banco_b = criar_conta_bancaria(self.loja_b, numero="22222-2")

        self.user = User.objects.create_user(username="ger@x.com", password="x")
        self.user.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(self.user, self.conta)
        self.api = APIClient()
        self.api.force_authenticate(self.user)

        self.vizinha = Conta.objects.create(nome="Vizinha", modulo_banco_ativo=True)
        self.loja_vizinha = criar_loja(
            conta=self.vizinha, nome_loja="Loja V", cidade="X", endereco="Y"
        )
        self.banco_vizinho = criar_conta_bancaria(self.loja_vizinha, numero="99999-9")

    def subir(self, conta_bancaria, conteudo, nome="extrato.ofx"):
        return self.api.post(
            "/api/v1/transacoes-bancarias/importar/",
            {
                "conta_bancaria": str(conta_bancaria.public_id),
                "arquivos": [SimpleUploadedFile(nome, conteudo)],
            },
            format="multipart",
        )


class ApiDoBancoTests(CenarioDoBanco):
    def test_importa_e_diz_o_que_entrou(self):
        arquivo = ofx([("20261001", "-10.00", "A"), ("20261002", "20.00", "B")], conta="11111-1")

        primeira = self.subir(self.banco_a, arquivo)
        segunda = self.subir(self.banco_a, arquivo)

        self.assertEqual(primeira.status_code, 200, primeira.data)
        self.assertEqual(primeira.data["importados"][0]["novas"], 2)
        self.assertEqual(segunda.data["importados"][0]["novas"], 0)
        self.assertEqual(segunda.data["importados"][0]["repetidas"], 2)

    def test_arquivo_recusado_nao_derruba_a_resposta(self):
        resp = self.subir(self.banco_a, b"nada a ver", nome="foto.jpg")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["importados"], [])
        self.assertEqual(resp.data["recusados"][0]["arquivo"], "foto.jpg")

    def test_nao_importa_na_conta_da_vizinha(self):
        resp = self.subir(self.banco_vizinho, ofx([("20261001", "-1.00", "A")], conta="99999-9"))
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(TransacaoBancaria.objects.exists())

    def test_lista_so_o_extrato_da_propria_empresa(self):
        importar_extrato(self.banco_a, "a.ofx", ofx([("20261001", "-1.00", "MEU")], conta="11111-1"))
        importar_extrato(
            self.banco_vizinho, "v.ofx", ofx([("20261001", "-1.00", "DELA")], conta="99999-9")
        )

        resp = self.api.get("/api/v1/transacoes-bancarias/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual([t["descricao"] for t in resp.data["results"]], ["MEU"])

    def test_filtros(self):
        importar_extrato(
            self.banco_a,
            "a.ofx",
            ofx([("20261001", "-1.00", "A"), ("20261010", "-2.00", "B")], conta="11111-1"),
        )
        importar_extrato(self.banco_b, "b.ofx", ofx([("20261005", "-3.00", "C")], conta="22222-2"))

        def descricoes(query):
            resp = self.api.get(f"/api/v1/transacoes-bancarias/?{query}")
            self.assertEqual(resp.status_code, 200, resp.data)
            return sorted(t["descricao"] for t in resp.data["results"])

        self.assertEqual(descricoes(f"loja={self.loja_b.public_id}"), ["C"])
        self.assertEqual(descricoes(f"conta_bancaria={self.banco_a.public_id}"), ["A", "B"])
        self.assertEqual(descricoes("de=2026-10-02&ate=2026-10-09"), ["C"])
        self.assertEqual(descricoes("pendente=true"), ["A", "B", "C"])

    def test_parametros_invalidos_dao_400(self):
        for query in ("loja=abc", "de=ontem", "ate=31/10/2026", "tipo=OUTRO"):
            with self.subTest(query=query):
                resp = self.api.get(f"/api/v1/transacoes-bancarias/?{query}")
                self.assertEqual(resp.status_code, 400)

    def test_classifica_pela_api(self):
        importar_extrato(self.banco_a, "a.ofx", ofx([("20261001", "-500.00", "ALUGUEL")], conta="11111-1"))
        transacao = TransacaoBancaria.objects.get()
        garantir_plano_de_contas(self.conta)
        aluguel = ElementoDeDespesa.objects.get(grupo__conta=self.conta, nome="Aluguel")

        resp = self.api.patch(
            f"/api/v1/transacoes-bancarias/{transacao.public_id}/",
            {"tipo": "PAGAMENTO", "elemento": str(aluguel.public_id)},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["elemento"]["nome"], "Aluguel")
        self.assertEqual(resp.data["iguais_classificadas"], 0)

    def test_classificacao_nao_mexe_no_que_veio_do_banco(self):
        importar_extrato(self.banco_a, "a.ofx", ofx([("20261001", "-500.00", "X")], conta="11111-1"))
        transacao = TransacaoBancaria.objects.get()

        self.api.patch(
            f"/api/v1/transacoes-bancarias/{transacao.public_id}/",
            {"tipo": None, "valor": "1.00", "descricao": "trocada", "data": "2020-01-01"},
            format="json",
        )

        transacao.refresh_from_db()
        self.assertEqual(
            (transacao.valor, transacao.descricao, transacao.data),
            (Decimal("-500.00"), "X", datetime.date(2026, 10, 1)),
        )

    def test_nao_aceita_elemento_ou_categoria_da_vizinha(self):
        importar_extrato(self.banco_a, "a.ofx", ofx([("20261001", "-500.00", "X")], conta="11111-1"))
        transacao = TransacaoBancaria.objects.get()
        garantir_plano_de_contas(self.vizinha)
        garantir_categorias_de_movimento(self.vizinha)
        elemento_dela = ElementoDeDespesa.objects.filter(grupo__conta=self.vizinha).first()
        categoria_dela = CategoriaDeMovimento.objects.filter(
            conta=self.vizinha, tipo=TRANSFERENCIA
        ).first()
        url = f"/api/v1/transacoes-bancarias/{transacao.public_id}/"

        resp = self.api.patch(
            url, {"tipo": "PAGAMENTO", "elemento": str(elemento_dela.public_id)}, format="json"
        )
        self.assertEqual(resp.status_code, 400)
        resp = self.api.patch(
            url,
            {"tipo": "TRANSFERENCIA", "categoria": str(categoria_dela.public_id)},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        transacao.refresh_from_db()
        self.assertIsNone(transacao.tipo)

    def test_categoria_do_tipo_errado_da_400_com_mensagem(self):
        importar_extrato(self.banco_a, "a.ofx", ofx([("20261001", "-500.00", "X")], conta="11111-1"))
        transacao = TransacaoBancaria.objects.get()
        garantir_categorias_de_movimento(self.conta)
        vendas = CategoriaDeMovimento.objects.get(conta=self.conta, tipo=RECEBIMENTO, nome__startswith="Vendas")

        resp = self.api.patch(
            f"/api/v1/transacoes-bancarias/{transacao.public_id}/",
            {"tipo": "TRANSFERENCIA", "categoria": str(vendas.public_id)},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("recebimento", resp.data["error"])

    def test_resumo_consolidado_tira_a_transferencia_entre_lojas(self):
        """Para a empresa, o unico gasto foi o aluguel."""
        importar_extrato(
            self.banco_a, "a.ofx", ofx([("20261002", "-500.00", "PIX LOJA B")], conta="11111-1")
        )
        importar_extrato(
            self.banco_b,
            "b.ofx",
            ofx([("20261002", "500.00", "PIX LOJA A"), ("20261003", "-500.00", "ALUGUEL")],
                conta="22222-2"),
        )
        garantir_plano_de_contas(self.conta)
        aluguel = ElementoDeDespesa.objects.get(grupo__conta=self.conta, nome="Aluguel")
        classificar(TransacaoBancaria.objects.get(descricao="ALUGUEL"), PAGAMENTO, elemento=aluguel)

        consolidado = self.api.get("/api/v1/transacoes-bancarias/resumo/").data
        so_b = self.api.get(f"/api/v1/transacoes-bancarias/resumo/?loja={self.loja_b.public_id}").data

        self.assertTrue(consolidado["consolidado"])
        self.assertEqual(consolidado["entradas"], "0.00")
        self.assertEqual(consolidado["saidas"], "-500.00")
        tipos = {bloco["tipo"]: bloco for bloco in consolidado["por_tipo"]}
        self.assertEqual(tipos["TRANSFERENCIA"]["valor"], "0.00")
        self.assertEqual(tipos["PAGAMENTO"]["categorias"][0]["nome"], "Operacional > Aluguel")

        self.assertFalse(so_b["consolidado"])
        self.assertEqual(so_b["entradas"], "500.00")
        self.assertEqual(so_b["saidas"], "-500.00")

    def test_resumo_conta_o_que_falta_classificar(self):
        importar_extrato(
            self.banco_a, "a.ofx", ofx([("20261001", "-7.00", "X"), ("20261001", "3.00", "Y")], conta="11111-1")
        )
        resumo = self.api.get("/api/v1/transacoes-bancarias/resumo/").data
        self.assertEqual(resumo["pendentes"], {"quantidade": 2, "valor": "-4.00"})

    def test_modulo_desligado_da_403(self):
        self.conta.modulo_banco_ativo = False
        self.conta.save()
        for url in (
            "/api/v1/transacoes-bancarias/",
            "/api/v1/contas-bancarias/",
            "/api/v1/categorias-de-movimento/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.api.get(url).status_code, 403)

    def test_funcionario_nao_ve_o_banco(self):
        """O login de conferencia confere o caixa; o banco e da gerencia."""
        funcionario = User.objects.create_user(username="f@x.com", password="x")
        funcionario.groups.add(Group.objects.get(name="Funcionario"))
        vincular_conta(funcionario, self.conta)
        api = APIClient()
        api.force_authenticate(funcionario)

        self.assertEqual(api.get("/api/v1/transacoes-bancarias/").status_code, 403)

    def test_so_com_o_banco_o_plano_de_contas_abre_para_a_gerencia(self):
        """O pagamento do extrato e classificado no plano de contas das notas,
        e a empresa pode ter so o banco ligado."""
        self.assertFalse(self.conta.modulo_notas_ativo)
        self.assertEqual(self.api.get("/api/v1/grupos-de-despesa/").status_code, 200)

        funcionario = User.objects.create_user(username="f2@x.com", password="x")
        funcionario.groups.add(Group.objects.get(name="Funcionario"))
        vincular_conta(funcionario, self.conta)
        api = APIClient()
        api.force_authenticate(funcionario)
        self.assertEqual(api.get("/api/v1/grupos-de-despesa/").status_code, 403)

    def test_me_diz_se_o_banco_esta_ligado(self):
        resp = self.api.get("/api/v1/user/me/")
        self.assertIs(resp.data["modulos"]["banco"], True)

        self.conta.modulo_banco_ativo = False
        self.conta.save()
        resp = self.api.get("/api/v1/user/me/")
        self.assertIs(resp.data["modulos"]["banco"], False)


class ContasBancariasApiTests(CenarioDoBanco):
    URL = "/api/v1/contas-bancarias/"

    def test_lista_so_as_contas_da_empresa(self):
        resp = self.api.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual({c["numero"] for c in resp.data}, {"11111-1", "22222-2"})
        self.assertEqual(resp.data[0]["banco_nome"], "Santander")

    def test_cria_conta(self):
        resp = self.api.post(
            self.URL,
            {"loja_id": str(self.loja_a.public_id), "banco": "PICPAY", "numero": "555-5"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["loja"]["nome_loja"], "Loja A")

    def test_nao_cria_na_loja_da_vizinha(self):
        resp = self.api.post(
            self.URL,
            {"loja_id": str(self.loja_vizinha.public_id), "banco": "PICPAY", "numero": "1"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(ContaBancaria.objects.filter(banco="PICPAY").exists())

    def test_recusa_conta_repetida(self):
        resp = self.api.post(
            self.URL,
            {
                "loja_id": str(self.loja_a.public_id), "banco": "SANTANDER",
                "agencia": "3301", "numero": "11111-1",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("já está cadastrada", str(resp.data))

    def test_conta_nao_muda_de_loja(self):
        resp = self.api.patch(
            f"{self.URL}{self.banco_a.public_id}/",
            {"loja_id": str(self.loja_b.public_id)},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_desativa_mas_nao_apaga(self):
        url = f"{self.URL}{self.banco_a.public_id}/"
        self.assertEqual(self.api.patch(url, {"ativo": False}, format="json").status_code, 200)
        self.assertEqual(self.api.delete(url).status_code, 405)

    def test_loja_com_conta_bancaria_nao_se_apaga(self):
        admin = User.objects.create_user(username="adm@x.com", password="x")
        admin.groups.add(Group.objects.get(name="Admin"))
        vincular_conta(admin, self.conta)
        api = APIClient()
        api.force_authenticate(admin)

        resp = api.delete(f"/api/v1/lojas/{self.loja_a.public_id}/")

        self.assertEqual(resp.status_code, 409)
        self.assertIn("contas bancarias", resp.data["error"])


class CategoriasDeMovimentoApiTests(CenarioDoBanco):
    URL = "/api/v1/categorias-de-movimento/"

    def test_lista_semeia_as_categorias(self):
        resp = self.api.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        nomes = {(c["tipo"], c["nome"]) for c in resp.data}
        self.assertIn(("TRANSFERENCIA", "Entre lojas"), nomes)
        self.assertIn(("RECEBIMENTO", "Vendas (Pix/maquininha)"), nomes)

    def test_cria_e_recusa_nome_repetido(self):
        corpo = {"tipo": "RECEBIMENTO", "nome": "Aluguel de sala"}
        self.assertEqual(self.api.post(self.URL, corpo, format="json").status_code, 201)
        self.assertEqual(self.api.post(self.URL, corpo, format="json").status_code, 400)

    def test_tipo_nao_muda(self):
        self.api.get(self.URL)
        categoria = CategoriaDeMovimento.objects.get(conta=self.conta, nome="Outros")
        resp = self.api.patch(
            f"{self.URL}{categoria.public_id}/", {"tipo": "TRANSFERENCIA"}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_renomear_entre_lojas_nao_quebra_o_pareamento(self):
        self.api.get(self.URL)
        entre_lojas = CategoriaDeMovimento.objects.get(conta=self.conta, entre_lojas=True)
        self.api.patch(f"{self.URL}{entre_lojas.public_id}/", {"nome": "Interna"}, format="json")

        importar_extrato(self.banco_a, "a.ofx", ofx([("20261002", "-9.00", "X")], conta="11111-1"))
        importar_extrato(self.banco_b, "b.ofx", ofx([("20261002", "9.00", "Y")], conta="22222-2"))

        self.assertEqual(
            TransacaoBancaria.objects.filter(categoria__nome="Interna").count(), 2
        )
