import io
import itertools
from datetime import date, datetime
from decimal import Decimal
from unittest import mock

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone
from PIL import Image

from app.models import Comprovante, Conta, ElementoDeDespesa
from app.services.comprovantes.categoria import aprender_categoria
from app.services.comprovantes.leitura import CAMPOS, LeituraIndisponivel
from app.services.comprovantes.registrar import NaoEComprovante, registrar_comprovante
from app.services.plano_de_contas import garantir_plano_de_contas

from .fabricas import conta_padrao, criar_loja

CNPJ_DA_LOJA = "11222333000181"
ARQUIVOS = itertools.count()


def leitura(**campos):
    """Uma leitura inventada no formato do Haiku (layout do Nubank)."""
    base = {campo: None for campo in CAMPOS}
    base.update(
        e_comprovante=True,
        valor="129.80",
        data_hora="2026-10-13T10:03:22",
        id_transacao="E00000000202610130000aaaa0001",
        pagador_nome="LOJA TESTE LTDA",
        pagador_documento=CNPJ_DA_LOJA,
        pagador_banco="NU PAGAMENTOS - IP",
        recebedor_nome="Maria da Silva",
        recebedor_documento="•••.111.222-••",
        recebedor_banco="STONE IP S.A.",
    )
    base.update(campos)
    return base


def leitor(dados):
    return lambda conteudo, tipo: dados


def foto():
    saida = io.BytesIO()
    Image.new("RGB", (400, 800), "white").save(saida, format="PNG")
    return saida.getvalue()


class RegistrarTests(TestCase):
    def setUp(self):
        self.conta = conta_padrao()
        self.loja = criar_loja(
            nome_loja="Loja A", cidade="Patos", endereco="Rua 1", cnpj=CNPJ_DA_LOJA
        )

    def registrar(self, dados=None, origem="EMAIL", conteudo=None, conta=None, **extra):
        # Cada chamada com um arquivo proprio: o mesmo arquivo duas vezes e
        # repetido por si so (test_mesmo_arquivo_*), e os outros testes nao
        # sao sobre isso.
        return registrar_comprovante(
            conta or self.conta,
            conteudo or b"%PDF-1.4 inventado " + str(next(ARQUIVOS)).encode(),
            "application/pdf",
            "comprovante.pdf",
            origem=origem,
            ler=leitor(dados or leitura()),
            **extra,
        )

    def test_email_com_leitura_completa_entra_ok_na_loja(self):
        comprovante = self.registrar(conteudo=b"%PDF-1.4 inventado")
        self.assertEqual(comprovante.situacao, Comprovante.Situacao.OK)
        self.assertEqual(comprovante.loja, self.loja)
        self.assertEqual(comprovante.valor, Decimal("129.80"))
        self.assertEqual(comprovante.recebedor_normalizado, "MARIA DA SILVA")
        self.assertEqual(bytes(comprovante.arquivo), b"%PDF-1.4 inventado")
        self.assertEqual(comprovante.leitura_bruta["valor"], "129.80")

    def test_sem_loja_cadastrada_entra_ok_sem_loja(self):
        comprovante = self.registrar(leitura(pagador_documento="99888777000166"))
        self.assertEqual(comprovante.situacao, Comprovante.Situacao.OK)
        self.assertIsNone(comprovante.loja)

    def test_mesmo_id_na_mesma_empresa_e_descartado(self):
        self.registrar()
        self.assertIsNone(self.registrar())
        self.assertEqual(Comprovante.objects.count(), 1)

    def test_mesmo_id_em_outra_empresa_entra(self):
        self.registrar()
        outra = Conta.objects.create(nome="Outra")
        self.assertIsNotNone(
            registrar_comprovante(
                outra, b"%PDF", "application/pdf", "c.pdf", origem="EMAIL", ler=leitor(leitura())
            )
        )

    def test_sem_id_nunca_e_repetido(self):
        self.registrar(leitura(id_transacao=None))
        self.assertIsNotNone(self.registrar(leitura(id_transacao=None)))

    def test_pix_recebido_vai_para_conferir(self):
        dados = leitura(
            pagador_documento="99888777000166", recebedor_documento="11.222.333/0001-81"
        )
        comprovante = self.registrar(dados)
        self.assertEqual(comprovante.situacao, Comprovante.Situacao.CONFERIR)
        self.assertIn("recebido", comprovante.motivo)

    def test_sem_valor_vai_para_conferir(self):
        comprovante = self.registrar(leitura(valor=None))
        self.assertEqual(comprovante.situacao, Comprovante.Situacao.CONFERIR)
        self.assertIn("valor", comprovante.motivo)

    def test_nao_e_comprovante_e_recusado_e_nada_e_gravado(self):
        # Um curriculo em PDF: nao entra nem em conferir.
        with self.assertRaises(NaoEComprovante):
            self.registrar(leitura(e_comprovante=False))
        self.assertFalse(Comprovante.objects.exists())

    def test_leitura_falhando_guarda_o_arquivo_para_conferir(self):
        def quebrado(conteudo, tipo):
            raise LeituraIndisponivel("a leitura falhou (APIConnectionError)")

        comprovante = registrar_comprovante(
            self.conta, b"%PDF", "application/pdf", "c.pdf", origem="EMAIL", ler=quebrado
        )
        self.assertEqual(comprovante.situacao, Comprovante.Situacao.CONFERIR)
        self.assertEqual(bytes(comprovante.arquivo), b"%PDF")
        self.assertIn("APIConnectionError", comprovante.motivo)

    def test_arquivo_grande_demais_nao_e_guardado(self):
        comprovante = registrar_comprovante(
            self.conta,
            b"0" * (10 * 1024 * 1024 + 1),
            "application/pdf",
            "grande.pdf",
            origem="EMAIL",
            ler=leitor(leitura()),
        )
        self.assertEqual(comprovante.situacao, Comprovante.Situacao.CONFERIR)
        self.assertIsNone(comprovante.arquivo)
        self.assertIn("10 MB", comprovante.motivo)

    def test_envio_pelo_site_com_valor_e_data_ja_fica_ok(self):
        comprovante = self.registrar(origem="SITE")
        self.assertEqual(comprovante.situacao, Comprovante.Situacao.OK)
        self.assertEqual(comprovante.motivo, "")

    def test_envio_pelo_site_sem_valor_vai_para_conferir(self):
        comprovante = self.registrar(leitura(valor=None), origem="SITE")
        self.assertEqual(comprovante.situacao, Comprovante.Situacao.CONFERIR)
        self.assertEqual(comprovante.motivo, "Não deu para ler o valor.")

    def test_loja_escolhida_no_site_vence_a_leitura(self):
        outra_loja = criar_loja(nome_loja="Loja B", cidade="Patos", endereco="Rua 2")
        comprovante = self.registrar(origem="SITE", loja=outra_loja)
        self.assertEqual(comprovante.loja, outra_loja)

    def test_foto_e_reduzida_antes_de_ler(self):
        tipos_lidos = []

        def espiao(conteudo, tipo):
            tipos_lidos.append(tipo)
            return leitura()

        registrar_comprovante(
            self.conta, foto(), "image/png", "foto.png", origem="SITE", ler=espiao
        )
        self.assertEqual(tipos_lidos, ["image/jpeg"])

    def test_categoria_aprendida_ja_chega(self):
        garantir_plano_de_contas(self.conta)
        aluguel = ElementoDeDespesa.objects.get(grupo__conta=self.conta, nome="Aluguel")
        aprender_categoria(
            Comprovante(
                conta=self.conta, recebedor_normalizado="MARIA DA SILVA", elemento=aluguel
            )
        )
        self.assertEqual(self.registrar().elemento, aluguel)

    def test_pessoal_nao_recebe_categoria_sugerida(self):
        """Mercado e farmacia nao sao despesa da loja: a regra do recebedor nao vale."""
        garantir_plano_de_contas(self.conta)
        aluguel = ElementoDeDespesa.objects.get(grupo__conta=self.conta, nome="Aluguel")
        aprender_categoria(
            Comprovante(
                conta=self.conta, recebedor_normalizado="MARIA DA SILVA", elemento=aluguel
            )
        )
        comprovante = self.registrar(pessoal=True)
        self.assertTrue(comprovante.pessoal)
        self.assertIsNone(comprovante.elemento)

    def test_id_com_espaco_ou_quebra_de_linha_e_o_mesmo_id(self):
        """Revisao final: o ID do PicPay vem quebrado em duas linhas no PDF."""
        self.registrar(leitura(id_transacao="E00000000202610130000aaaa0001"))
        self.assertIsNone(self.registrar(leitura(id_transacao="E00000000202610130000aaaa 0001")))
        self.assertIsNone(self.registrar(leitura(id_transacao="E00000000202610130000aaaa\n0001")))

    def test_id_igual_ao_mesmo_tempo_nao_estoura(self):
        """Foco da revisao 5: site e e-mail gravando o mesmo ID juntos."""
        with mock.patch(
            "app.services.comprovantes.registrar.Comprovante.save",
            side_effect=IntegrityError("duplicado"),
        ):
            self.assertIsNone(self.registrar())


def anotacao(**campos):
    """A foto de um bilhete a mao: pagamento em dinheiro, sem banco nem ID."""
    base = {campo: None for campo in CAMPOS}
    base.update(
        e_comprovante=True,
        e_anotacao=True,
        valor="3400.00",
        data_hora="2026-11-05",
        recebedor_nome="Ovo",
    )
    base.update(campos)
    return base


class AnotacaoAMaoTests(TestCase):
    """Pago em dinheiro nao tem comprovante: a dona anota valor, data e o que foi."""

    def setUp(self):
        self.conta = conta_padrao()
        self.loja = criar_loja(
            nome_loja="Loja A", cidade="Patos", endereco="Rua 1", cnpj=CNPJ_DA_LOJA
        )

    def registrar(self, dados, **extra):
        return registrar_comprovante(
            self.conta, foto(), "image/png", "bilhete.png",
            origem="SITE", ler=leitor(dados), **extra,
        )

    def test_valor_legivel_entra_ok_com_o_que_foi(self):
        comprovante = self.registrar(anotacao(), loja=self.loja)
        self.assertEqual(comprovante.situacao, Comprovante.Situacao.OK)
        self.assertEqual(comprovante.valor, Decimal("3400.00"))
        self.assertEqual(comprovante.recebedor_nome, "Ovo")
        self.assertEqual(comprovante.loja, self.loja)

    def test_a_data_e_a_do_bilhete_e_a_hora_e_a_do_envio(self):
        envio = timezone.make_aware(datetime(2026, 11, 6, 14, 37, 12))
        with mock.patch("app.services.comprovantes.registrar.timezone.now", return_value=envio):
            comprovante = self.registrar(anotacao())
        local = timezone.localtime(comprovante.data_hora)
        self.assertEqual(local.date(), date(2026, 11, 5))
        self.assertEqual((local.hour, local.minute), (14, 37))

    def test_valor_ilegivel_fica_em_duvida(self):
        comprovante = self.registrar(anotacao(valor=None))
        self.assertEqual(comprovante.situacao, Comprovante.Situacao.CONFERIR)
        self.assertEqual(comprovante.motivo, "Anotação à mão: não deu para ler o valor.")

    def test_data_ilegivel_fica_em_duvida(self):
        comprovante = self.registrar(anotacao(data_hora=None))
        self.assertEqual(comprovante.situacao, Comprovante.Situacao.CONFERIR)
        self.assertEqual(comprovante.motivo, "Anotação à mão: não deu para ler a data.")

    def test_o_que_foi_aprende_a_categoria(self):
        garantir_plano_de_contas(self.conta)
        aluguel = ElementoDeDespesa.objects.get(grupo__conta=self.conta, nome="Aluguel")
        aprender_categoria(
            Comprovante(conta=self.conta, recebedor_normalizado="OVO", elemento=aluguel)
        )
        self.assertEqual(self.registrar(anotacao()).elemento, aluguel)


class ComprovanteRepetidoTests(TestCase):
    """O Bradesco mostra tres numeros com cara de ID (Identificacao, No de
    controle, Documento) e a leitura pegava um diferente a cada envio: o mesmo
    Pix entrava duas vezes, os dois OK."""

    def setUp(self):
        self.conta = conta_padrao()
        criar_loja(nome_loja="Loja A", cidade="Patos", endereco="Rua 1", cnpj=CNPJ_DA_LOJA)

    def registrar(self, dados=None, conteudo=None, conta=None):
        return registrar_comprovante(
            conta or self.conta,
            conteudo or b"%PDF-1.4 " + str(next(ARQUIVOS)).encode(),
            "application/pdf",
            "comprovante.pdf",
            origem="EMAIL",
            ler=leitor(dados or leitura()),
        )

    def test_mesmo_arquivo_na_mesma_empresa_e_recusado(self):
        """Mesmo PDF, IDs lidos diferentes: o arquivo ja diz que e repetido."""
        self.registrar(leitura(id_transacao="E12345678202611031704Ab1Cd2Ef3Gh"), conteudo=b"%PDF bradesco")
        self.assertIsNone(
            self.registrar(leitura(id_transacao="900000000000000001"), conteudo=b"%PDF bradesco")
        )
        self.assertEqual(Comprovante.objects.count(), 1)

    def test_mesmo_arquivo_em_outra_empresa_entra(self):
        outra = Conta.objects.create(nome="Outra")
        self.registrar(conteudo=b"%PDF bradesco")
        self.assertIsNotNone(self.registrar(conteudo=b"%PDF bradesco", conta=outra))

    def test_mesmos_dados_com_outro_id_vai_para_conferir(self):
        """Arquivos diferentes (print e PDF do mesmo Pix) e ID lido diferente:
        nao soma ate alguem olhar."""
        primeiro = self.registrar(leitura(id_transacao="E12345678202611031704Ab1Cd2Ef3Gh"))
        segundo = self.registrar(leitura(id_transacao="900000000000000001"))

        self.assertEqual(primeiro.situacao, Comprovante.Situacao.OK)
        self.assertEqual(segundo.situacao, Comprovante.Situacao.CONFERIR)
        self.assertEqual(
            segundo.motivo,
            "Parece repetido de outro comprovante (mesmo valor, data e recebedor). "
            "Confira antes de somar.",
        )

    def test_mesmos_dados_sem_id_vai_para_conferir(self):
        self.registrar(leitura(id_transacao=None))
        self.assertEqual(
            self.registrar(leitura(id_transacao=None)).situacao, Comprovante.Situacao.CONFERIR
        )

    def test_repetido_de_um_descartado_entra_ok(self):
        primeiro = self.registrar(leitura(id_transacao="A1"))
        primeiro.situacao = Comprovante.Situacao.DESCARTADO
        primeiro.save()
        self.assertEqual(self.registrar(leitura(id_transacao="A2")).situacao, Comprovante.Situacao.OK)

    def test_outro_valor_ou_outra_hora_entra_ok(self):
        self.registrar(leitura(id_transacao="A1"))
        self.assertEqual(
            self.registrar(leitura(id_transacao="A2", valor="129.81")).situacao,
            Comprovante.Situacao.OK,
        )
        self.assertEqual(
            self.registrar(leitura(id_transacao="A3", data_hora="2026-10-13T10:04:22")).situacao,
            Comprovante.Situacao.OK,
        )

    def test_repetido_em_outra_empresa_entra_ok(self):
        outra = Conta.objects.create(nome="Outra")
        self.registrar(leitura(id_transacao="A1"))
        self.assertEqual(
            self.registrar(leitura(id_transacao="A2"), conta=outra).situacao,
            Comprovante.Situacao.OK,
        )
