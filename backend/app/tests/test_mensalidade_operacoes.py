from datetime import date, datetime
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase, override_settings

from app.models import Assinatura, Conta, Pagamento
from app.services.mensalidade import (
    RecusaDaMensalidade,
    Situacao,
    confirmar_pagamento,
    informar_pagamento,
    leitura_da_conta,
    recusar_pagamento,
    registrar_pagamento_manual,
)

from .fabricas import vincular_conta

BRASILIA = ZoneInfo("America/Sao_Paulo")


def momento(ano, mes, dia, hora=12, minuto=0):
    return datetime(ano, mes, dia, hora, minuto, tzinfo=BRASILIA)


@override_settings(MENSALIDADE_EMAIL_AVISO="dono@fechacaixa.io", FRONTEND_URL="https://fechacaixa.io")
class OperacoesTests(TestCase):
    def setUp(self):
        self.conta = Conta.objects.create(nome="Padaria Central")
        Assinatura.objects.filter(conta=self.conta).update(
            paga_ate=date(2026, 10, 10), dia_de_vencimento=10
        )
        # Relida do banco, como chega num pedido de verdade: a conta criada
        # acima guarda em memoria a assinatura que o save criou, de antes do
        # update.
        self.conta = Conta.objects.get(pk=self.conta.pk)
        self.gerente = User.objects.create_user(
            username="ger@padaria.com", email="ger@padaria.com", password="x",
            first_name="Ana",
        )
        self.gerente.groups.add(Group.objects.get_or_create(name="Gerente")[0])
        vincular_conta(self.gerente, self.conta)
        self.dono = User.objects.create_superuser(username="dono", password="x")

    def assinatura(self):
        return Assinatura.objects.get(conta=self.conta)


class LeituraTests(OperacoesTests):
    def test_conta_sem_assinatura_vale_como_isenta(self):
        Assinatura.objects.filter(conta=self.conta).delete()
        conta = Conta.objects.get(pk=self.conta.pk)
        self.assertEqual(leitura_da_conta(conta, momento(2026, 12, 1)).situacao, Situacao.ISENTA)

    def test_23h30_de_brasilia_no_vencimento_ainda_e_o_dia(self):
        # 23h30 em Brasilia no dia 10 ja e 02h30 do dia 11 em UTC.
        agora = datetime(2026, 10, 11, 2, 30, tzinfo=ZoneInfo("UTC"))
        leitura = leitura_da_conta(self.conta, agora)
        self.assertEqual((leitura.situacao, leitura.dias), (Situacao.VENCENDO, 0))

    def test_bloqueada_seis_dias_depois(self):
        self.assertEqual(
            leitura_da_conta(self.conta, momento(2026, 10, 16)).situacao, Situacao.BLOQUEADA
        )


class InformarPagamentoTests(OperacoesTests):
    def test_cria_o_aviso_e_manda_email_ao_dono(self):
        pagamento = informar_pagamento(self.conta, self.gerente, momento(2026, 10, 12, 14, 5))

        self.assertEqual(pagamento.status, Pagamento.Status.AGUARDANDO)
        self.assertEqual(pagamento.valor, Decimal("100.00"))
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["dono@fechacaixa.io"])
        self.assertIn("Padaria Central", email.subject)
        self.assertIn("https://fechacaixa.io/plataforma", email.body)
        self.assertIn("12/10/2026 às 14:05", email.body)

    def test_segundo_aviso_e_recusado(self):
        informar_pagamento(self.conta, self.gerente, momento(2026, 10, 12))
        with self.assertRaises(RecusaDaMensalidade) as recusa:
            informar_pagamento(self.conta, self.gerente, momento(2026, 10, 12))
        self.assertIn("aguardando confirmação", recusa.exception.mensagem)
        self.assertEqual(Pagamento.objects.count(), 1)

    def test_empresa_desativada_nao_informa(self):
        self.conta.ativo = False
        self.conta.save(update_fields=["ativo"])
        with self.assertRaises(RecusaDaMensalidade):
            informar_pagamento(self.conta, self.gerente, momento(2026, 10, 12))

    def test_isenta_nao_informa(self):
        Assinatura.objects.filter(conta=self.conta).update(isenta=True)
        with self.assertRaises(RecusaDaMensalidade):
            informar_pagamento(self.conta, self.gerente, momento(2026, 10, 12))

    def test_falha_no_email_nao_desfaz_o_aviso(self):
        with mock.patch(
            "app.services.mensalidade.avisos.EmailMessage.send", side_effect=OSError
        ):
            informar_pagamento(self.conta, self.gerente, momento(2026, 10, 12))
        self.assertEqual(Pagamento.objects.filter(status="AGUARDANDO").count(), 1)

    @override_settings(MENSALIDADE_EMAIL_AVISO="")
    def test_sem_email_do_dono_o_aviso_entra_mesmo_assim(self):
        informar_pagamento(self.conta, self.gerente, momento(2026, 10, 12))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Pagamento.objects.count(), 1)


class ConfirmarERecusarTests(OperacoesTests):
    def test_confirmar_na_carencia_mantem_o_dia_e_avisa_o_cliente(self):
        pagamento = informar_pagamento(self.conta, self.gerente, momento(2026, 10, 12))
        mail.outbox.clear()

        confirmar_pagamento(pagamento, self.dono, momento(2026, 10, 12, 15))

        self.assertEqual(self.assinatura().paga_ate, date(2026, 11, 10))
        pagamento.refresh_from_db()
        self.assertEqual(pagamento.status, Pagamento.Status.CONFIRMADO)
        self.assertEqual(pagamento.paga_ate_depois, date(2026, 11, 10))
        self.assertEqual(mail.outbox[0].to, ["ger@padaria.com"])
        self.assertEqual(mail.outbox[0].reply_to, ["dono@fechacaixa.io"])
        self.assertIn("10/11/2026", mail.outbox[0].body)

    def test_confirmar_bloqueada_recomeca_de_hoje(self):
        pagamento = informar_pagamento(self.conta, self.gerente, momento(2026, 10, 20))
        confirmar_pagamento(pagamento, self.dono, momento(2026, 10, 20, 15))

        assinatura = self.assinatura()
        self.assertEqual((assinatura.paga_ate, assinatura.dia_de_vencimento), (date(2026, 11, 20), 20))

    def test_confirmar_duas_vezes_avanca_uma_so(self):
        pagamento = informar_pagamento(self.conta, self.gerente, momento(2026, 10, 12))
        confirmar_pagamento(pagamento, self.dono, momento(2026, 10, 12, 15))

        with self.assertRaises(RecusaDaMensalidade):
            confirmar_pagamento(pagamento, self.dono, momento(2026, 10, 12, 15, 1))
        self.assertEqual(self.assinatura().paga_ate, date(2026, 11, 10))

    def test_recusar_nao_mexe_no_paga_ate_e_avisa_o_cliente(self):
        pagamento = informar_pagamento(self.conta, self.gerente, momento(2026, 10, 20))
        mail.outbox.clear()

        recusar_pagamento(pagamento, self.dono, momento(2026, 10, 20, 15))

        pagamento.refresh_from_db()
        self.assertEqual(pagamento.status, Pagamento.Status.RECUSADO)
        self.assertEqual(self.assinatura().paga_ate, date(2026, 10, 10))
        self.assertIn("comprovante", mail.outbox[0].body)
        self.assertEqual(
            leitura_da_conta(self.conta, momento(2026, 10, 20, 16)).situacao, Situacao.BLOQUEADA
        )

    def test_registrar_manual_confirma_direto_sem_email(self):
        pagamento = registrar_pagamento_manual(
            self.assinatura(), self.dono, paga_ate=date(2026, 12, 5),
            valor=Decimal("100.00"), observacao="pagou em dinheiro",
            agora=momento(2026, 10, 12),
        )

        self.assertEqual(
            (pagamento.status, pagamento.origem),
            (Pagamento.Status.CONFIRMADO, Pagamento.Origem.MANUAL),
        )
        assinatura = self.assinatura()
        self.assertEqual((assinatura.paga_ate, assinatura.dia_de_vencimento), (date(2026, 12, 5), 5))
        self.assertEqual(len(mail.outbox), 0)


INFINITEPAY = dict(INFINITEPAY_HANDLE="jose", INFINITEPAY_WEBHOOK_SEGREDO="s3gr3do")


@override_settings(**INFINITEPAY)
class LinkDePagamentoTests(OperacoesTests):
    def gerar(self, link="https://checkout/1", **kwargs):
        from app.services.mensalidade import link_de_pagamento

        with mock.patch("app.services.infinitepay.criar_link", return_value=link, **kwargs) as criar:
            pagamento = link_de_pagamento(self.conta, self.gerente, agora=momento(2026, 10, 12))
        return pagamento, criar

    def test_link_cria_pagamento_aberto_com_referencia_no_public_id(self):
        pagamento, criar = self.gerar()

        self.assertEqual(
            criar.call_args.kwargs,
            {
                "valor_centavos": 10000,
                "descricao": "Mensalidade FechaCaixa - Padaria Central",
                "referencia": str(pagamento.public_id),
            },
        )
        self.assertEqual(
            (pagamento.status, pagamento.origem, pagamento.link_pagamento),
            (Pagamento.Status.ABERTO, Pagamento.Origem.INFINITEPAY, "https://checkout/1"),
        )
        # Gerar o link nao e avisar que pagou: a situacao segue pelas datas.
        self.assertEqual(
            leitura_da_conta(self.conta, momento(2026, 10, 12)).situacao, Situacao.ATRASADA
        )

    def test_segundo_clique_reaproveita_o_link(self):
        primeiro, _ = self.gerar()
        segundo, criar = self.gerar(link="https://checkout/2")

        self.assertEqual(segundo.pk, primeiro.pk)
        criar.assert_not_called()
        self.assertEqual(Pagamento.objects.count(), 1)

    def test_valor_mudou_cancela_o_link_velho_e_gera_outro(self):
        velho, _ = self.gerar()
        Assinatura.objects.filter(conta=self.conta).update(valor_mensal=Decimal("120.00"))

        novo, criar = self.gerar(link="https://checkout/2")

        self.assertNotEqual(novo.pk, velho.pk)
        self.assertEqual(criar.call_args.kwargs["valor_centavos"], 12000)
        velho.refresh_from_db()
        self.assertEqual(velho.status, Pagamento.Status.CANCELADO)

    def test_infinitepay_fora_do_ar_nao_deixa_linha(self):
        from app.services.infinitepay import ErroDaInfinitePay

        with self.assertRaises(RecusaDaMensalidade) as recusa:
            self.gerar(side_effect=ErroDaInfinitePay("fora"))

        self.assertEqual(
            recusa.exception.mensagem,
            "Não foi possível gerar o link de pagamento agora. "
            "Tente de novo em alguns minutos.",
        )
        self.assertEqual(Pagamento.objects.count(), 0)

    def test_isenta_e_desativada_nao_geram_link(self):
        Assinatura.objects.filter(conta=self.conta).update(isenta=True)
        with self.assertRaises(RecusaDaMensalidade):
            self.gerar()
        Assinatura.objects.filter(conta=self.conta).update(isenta=False)
        self.conta.ativo = False
        self.conta.save(update_fields=["ativo"])
        with self.assertRaises(RecusaDaMensalidade):
            self.gerar()
        self.assertEqual(Pagamento.objects.count(), 0)

    @override_settings(INFINITEPAY_HANDLE="")
    def test_sem_infinitepay_configurada_recusa(self):
        with self.assertRaises(RecusaDaMensalidade):
            self.gerar()

    def test_ja_paguei_com_infinitepay_recusa_automatica(self):
        with self.assertRaises(RecusaDaMensalidade) as recusa:
            informar_pagamento(self.conta, self.gerente, agora=momento(2026, 10, 12))
        self.assertEqual(recusa.exception.mensagem, "A confirmação deste pagamento é automática.")
        self.assertEqual(Pagamento.objects.count(), 0)

    def test_registrar_manual_cancela_link_aberto(self):
        link, _ = self.gerar()
        registrar_pagamento_manual(
            self.assinatura(), self.dono, paga_ate=date(2026, 12, 5),
            valor=Decimal("100.00"), agora=momento(2026, 10, 12),
        )
        link.refresh_from_db()
        self.assertEqual(link.status, Pagamento.Status.CANCELADO)

    def test_confirmar_aviso_cancela_link_aberto(self):
        with override_settings(INFINITEPAY_HANDLE=""):
            aviso = informar_pagamento(self.conta, self.gerente, agora=momento(2026, 10, 11))
        link, _ = self.gerar()

        confirmar_pagamento(aviso, self.dono, agora=momento(2026, 10, 12))

        link.refresh_from_db()
        self.assertEqual(link.status, Pagamento.Status.CANCELADO)
