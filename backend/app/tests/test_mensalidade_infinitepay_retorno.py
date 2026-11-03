from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from app.models import Assinatura, Conta, Pagamento
from app.services.infinitepay import ErroDaInfinitePay
from app.services.mensalidade import Situacao, leitura_da_conta, registrar_pagamento_manual
from app.services.mensalidade.infinitepay_retorno import (
    PAGO_EM_CANCELADO,
    conferir_pendentes,
    processar,
)

from .test_mensalidade_operacoes import INFINITEPAY, OperacoesTests, momento

RETORNO = "/api/v1/infinitepay/retorno/"
PAGO = {"pago": True, "valor_centavos": 10000, "meio": "pix"}


def conferir_responde(resposta=PAGO, **kwargs):
    return mock.patch("app.services.infinitepay.conferir", return_value=resposta, **kwargs)


@override_settings(**INFINITEPAY)
class RetornoTests(OperacoesTests):
    def link(self, **campos):
        return Pagamento.objects.create(
            assinatura=self.assinatura(),
            status=Pagamento.Status.ABERTO,
            origem=Pagamento.Origem.INFINITEPAY,
            valor=Decimal("100.00"),
            informado_por=self.gerente,
            informado_em=momento(2026, 10, 11),
            link_pagamento="https://checkout/1",
            **campos,
        )

    def processar(self, pagamento, transacao="t1", slug="f1", agora=None):
        return processar(
            referencia=str(pagamento.public_id),
            transacao=transacao,
            slug=slug,
            agora=agora or momento(2026, 10, 12),
        )

    def test_aviso_pago_confirma_avanca_paga_ate_e_manda_email(self):
        link = self.link()
        with conferir_responde() as conferir:
            self.assertEqual(self.processar(link), "confirmada")

        conferir.assert_called_once_with(referencia=str(link.public_id), transacao="t1", slug="f1")
        link.refresh_from_db()
        self.assertEqual(link.status, Pagamento.Status.CONFIRMADO)
        self.assertIsNone(link.resolvido_por)
        self.assertEqual(link.paga_ate_depois, date(2026, 11, 10))
        self.assertEqual(self.assinatura().paga_ate, date(2026, 11, 10))
        self.assertEqual((link.ip_transacao, link.ip_fatura), ("t1", "f1"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ger@padaria.com"])

    def test_transacao_longa_nao_quebra_o_aviso(self):
        link = self.link()
        transacao, fatura = "t" * 200, "f" * 300
        with conferir_responde():
            self.assertEqual(self.processar(link, transacao=transacao, slug=fatura), "confirmada")
        link.refresh_from_db()
        self.assertEqual((link.ip_transacao, link.ip_fatura), (transacao, fatura))

    def test_aviso_com_valor_diferente_ignora(self):
        link = self.link()
        with conferir_responde({"pago": True, "valor_centavos": 100, "meio": "pix"}):
            self.assertEqual(self.processar(link), "ignorada")
        link.refresh_from_db()
        self.assertEqual(link.status, Pagamento.Status.ABERTO)
        self.assertEqual(self.assinatura().paga_ate, date(2026, 10, 10))

    def test_aviso_nao_pago_fica_pendente_e_guarda_transacao(self):
        link = self.link()
        with conferir_responde({"pago": False, "valor_centavos": 10000, "meio": ""}):
            self.assertEqual(self.processar(link), "pendente")
        link.refresh_from_db()
        self.assertEqual(
            (link.status, link.ip_transacao, link.ip_fatura),
            (Pagamento.Status.ABERTO, "t1", "f1"),
        )

    def test_infinitepay_fora_sobe_mas_guarda_transacao(self):
        link = self.link()
        with conferir_responde(side_effect=ErroDaInfinitePay("fora")), self.assertRaises(
            ErroDaInfinitePay
        ):
            self.processar(link)
        link.refresh_from_db()
        self.assertEqual(link.ip_transacao, "t1")

    def test_aviso_repetido_em_confirmado_e_ja_resolvida(self):
        link = self.link()
        with conferir_responde():
            self.processar(link)
        with conferir_responde() as conferir:
            self.assertEqual(self.processar(link), "ja_resolvida")
        conferir.assert_not_called()
        self.assertEqual(self.assinatura().paga_ate, date(2026, 11, 10))

    def test_pago_em_cancelado_avisa_o_dono_uma_vez(self):
        link = self.link()
        registrar_pagamento_manual(
            self.assinatura(), self.dono, paga_ate=date(2026, 12, 5),
            valor=Decimal("100.00"), agora=momento(2026, 10, 12),
        )
        with conferir_responde():
            self.assertEqual(self.processar(link), "paga_em_cancelada")
            self.assertEqual(self.processar(link), "paga_em_cancelada")

        link.refresh_from_db()
        self.assertEqual(link.status, Pagamento.Status.CANCELADO)
        self.assertEqual(link.observacao, PAGO_EM_CANCELADO)
        self.assertEqual(self.assinatura().paga_ate, date(2026, 12, 5))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["dono@fechacaixa.io"])
        self.assertIn("Padaria Central", mail.outbox[0].subject)

    def test_referencia_que_nao_e_uuid_ou_nao_e_link_ignora(self):
        aviso = Pagamento.objects.create(
            assinatura=self.assinatura(),
            status=Pagamento.Status.AGUARDANDO,
            origem=Pagamento.Origem.JA_PAGUEI,
            valor=Decimal("100.00"),
        )
        with conferir_responde() as conferir:
            self.assertEqual(
                processar(referencia="nao-e-uuid", transacao="t", slug="s", agora=momento(2026, 10, 12)),
                "ignorada",
            )
            self.assertEqual(self.processar(aviso), "ignorada")
        conferir.assert_not_called()
        aviso.refresh_from_db()
        self.assertEqual(aviso.status, Pagamento.Status.AGUARDANDO)

    def test_varredura_so_confere_quem_ja_recebeu_aviso(self):
        com_aviso = self.link(ip_transacao="t1", ip_fatura="f1")
        with conferir_responde() as conferir:
            self.assertEqual(conferir_pendentes(), {"confirmada": 1})
        conferir.assert_called_once_with(
            referencia=str(com_aviso.public_id), transacao="t1", slug="f1"
        )

    def test_varredura_ignora_link_sem_aviso(self):
        self.link()
        with conferir_responde() as conferir:
            self.assertEqual(conferir_pendentes(), {})
        conferir.assert_not_called()

    def test_varredura_segue_depois_de_erro(self):
        self.link(ip_transacao="t1", ip_fatura="f1")
        outra = self.link(ip_transacao="t2", ip_fatura="f2")
        respostas = {"t1": ErroDaInfinitePay("fora"), "t2": PAGO}

        def conferir(*, referencia, transacao, slug):
            r = respostas[transacao]
            if isinstance(r, Exception):
                raise r
            return r

        with mock.patch("app.services.infinitepay.conferir", side_effect=conferir):
            self.assertEqual(conferir_pendentes(), {"erro": 1, "confirmada": 1})
        outra.refresh_from_db()
        self.assertEqual(outra.status, Pagamento.Status.CONFIRMADO)


@override_settings(**INFINITEPAY)
class RetornoViewTests(RetornoTests):
    def avisar(self, corpo, t="s3gr3do"):
        url = RETORNO if t is None else f"{RETORNO}?t={t}"
        return APIClient().post(url, corpo, format="json")

    def corpo(self, link):
        return {
            "order_nsu": str(link.public_id),
            "transaction_nsu": "t1",
            "invoice_slug": "f1",
        }

    def test_retorno_sem_t_401(self):
        self.assertEqual(self.avisar(self.corpo(self.link()), t=None).status_code, 401)

    def test_retorno_t_errado_401(self):
        self.assertEqual(self.avisar(self.corpo(self.link()), t="x").status_code, 401)

    def test_retorno_ok_200_confirmada(self):
        link = self.link()
        with conferir_responde():
            resp = self.avisar(self.corpo(link))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, {"ok": True, "resultado": "confirmada"})

    def test_retorno_infinitepay_fora_400(self):
        with conferir_responde(side_effect=ErroDaInfinitePay("fora")):
            resp = self.avisar(self.corpo(self.link()))
        self.assertEqual(resp.status_code, 400)

    def test_retorno_sem_order_nsu_200_ignorada(self):
        resp = self.avisar({"transaction_nsu": "t1"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["resultado"], "ignorada")

    def test_retorno_nao_passa_pela_trava(self):
        Assinatura.objects.filter(conta=self.conta).update(
            paga_ate=timezone.localdate() - timedelta(days=10)
        )
        self.assertEqual(leitura_da_conta(self.conta).situacao, Situacao.BLOQUEADA)
        link = self.link()

        with conferir_responde():
            resp = self.avisar(self.corpo(link))

        self.assertEqual(resp.status_code, 200)
        # Relida: a conta em memoria guarda a assinatura de antes do aviso.
        conta = Conta.objects.get(pk=self.conta.pk)
        self.assertEqual(leitura_da_conta(conta).situacao, Situacao.EM_DIA)

    # Os casos do servico ja rodam na classe de cima.
    test_aviso_pago_confirma_avanca_paga_ate_e_manda_email = None
    test_aviso_com_valor_diferente_ignora = None
    test_transacao_longa_nao_quebra_o_aviso = None
    test_aviso_nao_pago_fica_pendente_e_guarda_transacao = None
    test_infinitepay_fora_sobe_mas_guarda_transacao = None
    test_aviso_repetido_em_confirmado_e_ja_resolvida = None
    test_pago_em_cancelado_avisa_o_dono_uma_vez = None
    test_referencia_que_nao_e_uuid_ou_nao_e_link_ignora = None
    test_varredura_so_confere_quem_ja_recebeu_aviso = None
    test_varredura_ignora_link_sem_aviso = None
    test_varredura_segue_depois_de_erro = None


class ComandoTests(OperacoesTests):
    @override_settings(INFINITEPAY_HANDLE="")
    def test_comando_uma_volta_sem_configuracao_nao_chama_nada(self):
        saida = StringIO()
        with mock.patch("app.services.infinitepay.conferir") as conferir:
            call_command("conferir_pagamentos_infinitepay", stdout=saida)
        conferir.assert_not_called()
        self.assertIn("não está configurada", saida.getvalue())

    @override_settings(**INFINITEPAY)
    def test_comando_uma_volta_confere(self):
        saida = StringIO()
        with mock.patch(
            "app.services.mensalidade.infinitepay_retorno.conferir_pendentes",
            return_value={"confirmada": 1},
        ) as varrer:
            call_command("conferir_pagamentos_infinitepay", stdout=saida)
        varrer.assert_called_once_with()
        self.assertIn("confirmada", saida.getvalue())
