"""Os tres e-mails da conexao com a Pluggy: conectou, caiu, voltou.

Um por mudanca de estado, nunca um por rodada. Falha de SMTP vai para o log e
segue: o estado ja esta gravado e a busca nao pode parar por e-mail.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage

logger = logging.getLogger(__name__)


def _destinatarios():
    email = settings.PLUGGY_EMAIL_AVISO or settings.MENSALIDADE_EMAIL_AVISO
    return [email] if email else []


def _enviar(assunto, corpo):
    para = _destinatarios()
    if not para:
        logger.warning("E-mail da Pluggy sem destinatario: %s", assunto)
        return False
    try:
        EmailMessage(assunto, corpo, settings.DEFAULT_FROM_EMAIL, para).send()
        return True
    except Exception:
        logger.exception("Falha ao enviar e-mail da Pluggy: %s", assunto)
        return False


def _link():
    return f"{settings.FRONTEND_URL}/plataforma/pluggy"


def avisar_conectada(conexao, transacoes):
    empresa = conexao.conta.nome
    corpo = (
        f"A Pluggy da {empresa} está conectada.\n\n"
        f"Na primeira busca vieram {transacoes} transações.\n\n"
        f"Acompanhe em:\n{_link()}\n"
    )
    return _enviar(f"Pluggy conectada: {empresa} — {transacoes} transações", corpo)


def avisar_caiu(conexao):
    empresa = conexao.conta.nome
    motivo = conexao.ultimo_erro
    corpo = (
        f"A Pluggy da {empresa} caiu.\n\n"
        f"Motivo: {motivo}\n\n"
        f"Veja e corrija em:\n{_link()}\n"
    )
    return _enviar(f"Pluggy caiu: {empresa} — {motivo}", corpo)


def avisar_voltou(conexao):
    empresa = conexao.conta.nome
    corpo = (
        f"A Pluggy da {empresa} voltou a atualizar.\n\n"
        "Motivo: a busca deu certo de novo e os bancos estão em dia.\n\n"
        f"Acompanhe em:\n{_link()}\n"
    )
    return _enviar(f"Pluggy voltou: {empresa}", corpo)
