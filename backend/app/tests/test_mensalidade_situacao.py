from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from app.services.mensalidade.situacao import (
    TRAVAM_O_PAINEL,
    Situacao,
    ler_situacao,
    novo_paga_ate,
)

BRASILIA = ZoneInfo("America/Sao_Paulo")
PAGA_ATE = date(2026, 10, 10)


def ler(hoje, **mudanca):
    campos = {
        "conta_ativa": True,
        "isenta": False,
        "nunca_bloquear": False,
        "paga_ate": PAGA_ATE,
        "aviso_em": None,
        "hoje": hoje,
        "agora": datetime(hoje.year, hoje.month, hoje.day, 12, 0, tzinfo=BRASILIA),
    }
    campos.update(mudanca)
    return ler_situacao(**campos)


class FronteirasDeDataTests(SimpleTestCase):
    def test_seis_dias_antes_esta_em_dia(self):
        self.assertEqual(ler(PAGA_ATE - timedelta(days=6)).situacao, Situacao.EM_DIA)

    def test_cinco_dias_antes_ja_esta_vencendo(self):
        leitura = ler(PAGA_ATE - timedelta(days=5))
        self.assertEqual((leitura.situacao, leitura.dias), (Situacao.VENCENDO, 5))

    def test_no_dia_do_vencimento_ainda_esta_vencendo(self):
        leitura = ler(PAGA_ATE)
        self.assertEqual((leitura.situacao, leitura.dias), (Situacao.VENCENDO, 0))

    def test_um_dia_depois_esta_atrasada(self):
        leitura = ler(PAGA_ATE + timedelta(days=1))
        self.assertEqual((leitura.situacao, leitura.dias), (Situacao.ATRASADA, 1))

    def test_cinco_dias_depois_ainda_e_carencia(self):
        self.assertEqual(ler(PAGA_ATE + timedelta(days=5)).situacao, Situacao.ATRASADA)

    def test_seis_dias_depois_bloqueia(self):
        leitura = ler(PAGA_ATE + timedelta(days=6))
        self.assertEqual((leitura.situacao, leitura.dias), (Situacao.BLOQUEADA, 6))
        self.assertIn(leitura.situacao, TRAVAM_O_PAINEL)

    def test_bloqueia_em_e_o_primeiro_dia_bloqueado(self):
        self.assertEqual(ler(PAGA_ATE).bloqueia_em, date(2026, 10, 16))


class ExcecoesTests(SimpleTestCase):
    def test_nunca_bloquear_fica_atrasada_para_sempre(self):
        leitura = ler(PAGA_ATE + timedelta(days=60), nunca_bloquear=True)
        self.assertEqual((leitura.situacao, leitura.dias), (Situacao.ATRASADA, 60))
        self.assertIsNone(leitura.bloqueia_em)

    def test_isenta_sempre(self):
        self.assertEqual(
            ler(PAGA_ATE + timedelta(days=90), isenta=True).situacao, Situacao.ISENTA
        )

    def test_desativada_ganha_de_tudo(self):
        leitura = ler(PAGA_ATE, conta_ativa=False, isenta=True)
        self.assertEqual(leitura.situacao, Situacao.DESATIVADA)
        self.assertIn(leitura.situacao, TRAVAM_O_PAINEL)

    def test_aviso_de_dois_dias_segura_o_bloqueio(self):
        hoje = PAGA_ATE + timedelta(days=10)
        agora = datetime(hoje.year, hoje.month, hoje.day, 12, 0, tzinfo=BRASILIA)
        leitura = ler(hoje, agora=agora, aviso_em=agora - timedelta(days=2))
        self.assertEqual(leitura.situacao, Situacao.AGUARDANDO)

    def test_aviso_de_quatro_dias_nao_segura_mais(self):
        hoje = PAGA_ATE + timedelta(days=10)
        agora = datetime(hoje.year, hoje.month, hoje.day, 12, 0, tzinfo=BRASILIA)
        leitura = ler(hoje, agora=agora, aviso_em=agora - timedelta(days=4))
        self.assertEqual(leitura.situacao, Situacao.BLOQUEADA)


class NovoPagaAteTests(SimpleTestCase):
    def test_em_dia_ou_na_carencia_mantem_o_dia(self):
        self.assertEqual(
            novo_paga_ate(
                paga_ate=date(2026, 10, 10), dia_de_vencimento=10,
                estava_bloqueada=False, hoje=date(2026, 10, 12),
            ),
            (date(2026, 11, 10), 10),
        )

    def test_dezembro_vira_janeiro(self):
        self.assertEqual(
            novo_paga_ate(
                paga_ate=date(2026, 12, 20), dia_de_vencimento=20,
                estava_bloqueada=False, hoje=date(2026, 12, 20),
            ),
            (date(2027, 1, 20), 20),
        )

    def test_bloqueada_recomeca_de_hoje(self):
        self.assertEqual(
            novo_paga_ate(
                paga_ate=date(2026, 9, 1), dia_de_vencimento=1,
                estava_bloqueada=True, hoje=date(2026, 10, 30),
            ),
            (date(2026, 11, 28), 28),
        )
