"""Tasks Celery de notificacao (rodam no worker; sincronas nos testes)."""

import logging

from celery import shared_task
from django.conf import settings
from django.contrib.auth.models import User
from django.core import signing
from django.core.mail import send_mail


logger = logging.getLogger(__name__)

# Trocar um salt invalida os links que ja sairam por e-mail e ainda estao
# dentro dos 3 dias. Isto foi trocado antes do primeiro deploy, com o banco
# sem nenhuma conta pendente — de agora em diante, mudar aqui quebra links de
# gente que esta esperando.
SALT_CONFIRMACAO = "fechacaixa-confirmacao-conta"
VALIDADE_TOKEN_SEGUNDOS = 60 * 60 * 24 * 3  # 3 dias


def gerar_token_confirmacao(user_id):
    return signing.dumps({"user_id": user_id}, salt=SALT_CONFIRMACAO)


def validar_token_confirmacao(token):
    """Retorna o user_id do token, ou levanta signing.BadSignature/SignatureExpired."""
    dados = signing.loads(
        token, salt=SALT_CONFIRMACAO, max_age=VALIDADE_TOKEN_SEGUNDOS
    )
    return dados["user_id"]


@shared_task
def enviar_email_confirmacao(user_id):
    usuario = User.objects.filter(id=user_id).first()
    if not usuario or usuario.is_active:
        return False

    link = f"{settings.FRONTEND_URL}/confirmar-conta/{gerar_token_confirmacao(usuario.id)}"
    nome = usuario.first_name or usuario.username
    mensagem = (
        f"Ola, {nome}!\n\n"
        f"Confirme sua conta no FechaCaixa clicando no link:\n{link}\n\n"
        "O link vale por 3 dias. Se voce nao criou esta conta, ignore este email."
    )
    # Direto pelo send_mail: o despachante que existia aqui escolhia entre
    # e-mail e um canal de WhatsApp que nunca foi construido, lendo o modelo de
    # preferencias — os dois sairam com o Unistock. Com um canal so, a escolha
    # nao tinha o que decidir.
    return send_mail(
        "Confirme sua conta no FechaCaixa",
        mensagem,
        settings.DEFAULT_FROM_EMAIL,
        [usuario.email],
        fail_silently=False,
    ) > 0


@shared_task
def enviar_email_definir_senha(user_id):
    """Manda o link de definir senha (1o acesso da loja ou 'esqueci a senha')."""
    from .tokens import gerar_token_senha

    usuario = User.objects.filter(id=user_id).first()
    if not usuario or not usuario.email:
        return False

    link = f"{settings.FRONTEND_URL}/redefinir-senha/{gerar_token_senha(usuario)}"
    mensagem = (
        "Ola!\n\n"
        "Para acessar o FechaCaixa, defina a senha desta conta pelo link:\n"
        f"{link}\n\n"
        "O link vale por 3 dias e pode ser usado uma unica vez.\n"
        "Se voce nao pediu isso, ignore este email."
    )
    send_mail(
        "Defina a senha da sua conta no FechaCaixa",
        mensagem,
        settings.DEFAULT_FROM_EMAIL,
        [usuario.email],
        fail_silently=False,
    )
    return True
