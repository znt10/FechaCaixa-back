import io
from decimal import Decimal
from unittest import mock

from django.db import IntegrityError
from django.test import TestCase
from PIL import Image

from app.models import Comprovante, Conta, ElementoDeDespesa
from app.services.comprovantes.categoria import aprender_categoria
from app.services.comprovantes.leitura import CAMPOS, LeituraIndisponivel
from app.services.comprovantes.registrar import registrar_comprovante
from app.services.plano_de_contas import garantir_plano_de_contas

from .fabricas import conta_padrao, criar_loja

CNPJ_DA_LOJA = "11222333000181"


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

    def registrar(self, dados=None, origem="EMAIL", **extra):
        return registrar_comprovante(
            self.conta,
            b"%PDF-1.4 inventado",
            "application/pdf",
            "comprovante.pdf",
            origem=origem,
            ler=leitor(dados or leitura()),
            **extra,
        )

    def test_email_com_leitura_completa_entra_ok_na_loja(self):
        comprovante = self.registrar()
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

    def test_nao_e_comprovante_vai_para_conferir(self):
        comprovante = self.registrar(leitura(e_comprovante=False))
        self.assertEqual(comprovante.situacao, Comprovante.Situacao.CONFERIR)

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

    def test_envio_pelo_site_sempre_espera_confirmacao(self):
        comprovante = self.registrar(origem="SITE")
        self.assertEqual(comprovante.situacao, Comprovante.Situacao.CONFERIR)
        self.assertEqual(comprovante.motivo, "")

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
