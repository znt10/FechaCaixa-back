"""Os tres e-mails da mensalidade, enviados no proprio pedido.

Sem tarefa agendada: cada um sai no clique de alguem ("Ja paguei",
Confirmar, Recusar). Falha de SMTP registra no log e segue — o aviso e a
confirmacao ja estao gravados, e desfaze-los por um e-mail que nao saiu
trancaria de novo quem ja pagou.
"""

import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMessage
from django.utils import timezone

from app.grupos import GRUPO_ADMIN, GRUPO_GERENTE

logger = logging.getLogger(__name__)


def em_reais(valor):
    texto = f"{valor:,.2f}"
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _data(data):
    return data.strftime("%d/%m/%Y")


def _data_e_hora(momento):
    local = timezone.localtime(momento)
    return f"{local.strftime('%d/%m/%Y')} às {local.strftime('%H:%M')}"


def _enviar(assunto, corpo, para, responder_para=None):
    para = [email for email in para if email]
    if not para:
        logger.warning("E-mail da mensalidade sem destinatario: %s", assunto)
        return False
    try:
        EmailMessage(
            assunto,
            corpo,
            settings.DEFAULT_FROM_EMAIL,
            para,
            reply_to=[responder_para] if responder_para else None,
        ).send()
        return True
    except Exception:
        logger.exception("Falha ao enviar e-mail da mensalidade: %s", assunto)
        return False


def emails_da_gerencia(conta):
    return list(
        User.objects.filter(
            perfil__conta=conta,
            is_active=True,
            groups__name__in=[GRUPO_GERENTE, GRUPO_ADMIN],
        )
        .exclude(email="")
        .values_list("email", flat=True)
        .distinct()
    )


def avisar_dono(pagamento):
    conta = pagamento.assinatura.conta
    quem = pagamento.informado_por
    nome = (quem.first_name or quem.email or quem.username) if quem else "Alguém da empresa"
    corpo = (
        f"{nome} informou que pagou a mensalidade da {conta.nome}.\n\n"
        f"Valor: {em_reais(pagamento.valor)}\n"
        f"Informado em: {_data_e_hora(pagamento.informado_em)}\n\n"
        "Confira no seu banco e confirme ou recuse em:\n"
        f"{settings.FRONTEND_URL}/plataforma\n"
    )
    return _enviar(
        f"Pagamento informado: {conta.nome} - {em_reais(pagamento.valor)}",
        corpo,
        [settings.MENSALIDADE_EMAIL_AVISO],
    )


def avisar_confirmacao(pagamento):
    conta = pagamento.assinatura.conta
    corpo = (
        f"Recebemos o pagamento de {em_reais(pagamento.valor)} da mensalidade da "
        f"{conta.nome}.\n\n"
        f"O acesso ao FechaCaixa está pago até {_data(pagamento.paga_ate_depois)}.\n"
    )
    return _enviar(
        "Pagamento confirmado - FechaCaixa",
        corpo,
        emails_da_gerencia(conta),
        responder_para=settings.MENSALIDADE_EMAIL_AVISO,
    )


def avisar_recusa(pagamento):
    conta = pagamento.assinatura.conta
    corpo = (
        f"Não encontramos o pagamento de {em_reais(pagamento.valor)} informado em "
        f"{_data(timezone.localtime(pagamento.informado_em))} para a {conta.nome}.\n\n"
        "Se você já pagou, responda este e-mail com o comprovante.\n"
    )
    return _enviar(
        "Não encontramos seu pagamento - FechaCaixa",
        corpo,
        emails_da_gerencia(conta),
        responder_para=settings.MENSALIDADE_EMAIL_AVISO,
    )
