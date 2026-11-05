"""Gerentes criados pela Plataforma (aba Acessos), sem o admin do Django.

A senha e opcional: preenchida, o gerente ja entra com ela e ninguem recebe
e-mail; em branco, ele nasce sem senha e inativo, e recebe o mesmo link do
"Esqueci a senha" para criar a propria (o dono da plataforma nao fica sabendo).
O `definir-senha` e quem ativa a conta.
"""

import logging

from django.contrib.auth.models import Group, User
from rest_framework.exceptions import ValidationError

from app.grupos import GRUPO_GERENTE
from app.models import PerfilUsuario
from app.notifications.tasks import enviar_email_definir_senha

logger = logging.getLogger(__name__)

SENHA_MINIMA = 6

ATIVO = "ATIVO"
AGUARDANDO_SENHA = "AGUARDANDO_SENHA"
DESATIVADO = "DESATIVADO"


def criar_gerente(conta, nome, email, senha=None):
    """Cria o gerente ja vinculado a `conta`. Nao manda e-mail: quem chama
    decide quando (depois do commit, para o link nao apontar para um usuario
    que a transacao desfez)."""
    email = (email or "").strip().lower()
    # Mesma regra e mensagem do funcionario: username=email neste projeto, e
    # sem a checagem o create estouraria com IntegrityError (500).
    if User.objects.filter(username=email).exists() or User.objects.filter(
        email__iexact=email
    ).exists():
        raise ValidationError({"email": ["Este email ja esta em uso."]})
    senha = (senha or "").strip() and senha
    if senha and len(senha) < SENHA_MINIMA:
        raise ValidationError({"senha": ["A senha deve conter pelo menos 6 caracteres."]})

    user = User(username=email, email=email, first_name=(nome or "").strip())
    if senha:
        user.set_password(senha)
        user.is_active = True
    else:
        user.set_unusable_password()
        user.is_active = False
    user.save()
    user.groups.add(Group.objects.get_or_create(name=GRUPO_GERENTE)[0])
    PerfilUsuario.objects.create(user=user, conta=conta)
    return user


def mandar_link(user):
    """Manda o link de criar a senha. False se o e-mail nao saiu.

    Chamado direto, e nao pelo `.delay`: em producao o Celery roda eager, e
    um SMTP fora do ar viraria 500 depois de o gerente ja estar criado.
    """
    try:
        enviar_email_definir_senha(user.id)
    except Exception:
        logger.exception("Nao deu para mandar o link de senha para o usuario %s.", user.id)
        return False
    return True


def situacao_do_gerente(user):
    if not user.has_usable_password():
        return AGUARDANDO_SENHA
    if not user.is_active:
        return DESATIVADO
    return ATIVO
