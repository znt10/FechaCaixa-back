"""O que a tela Plataforma mostra de cada empresa."""

from django.utils import timezone

from app.models import Conta, Pagamento

from .operacoes import assinatura_da, aviso_pendente, leitura_da_conta
from .situacao import Situacao

# Quem precisa do dono primeiro: o aviso a conferir, o bloqueado, o atrasado.
ORDEM = {
    Situacao.AGUARDANDO: 0,
    Situacao.BLOQUEADA: 1,
    Situacao.ATRASADA: 2,
    Situacao.VENCENDO: 3,
}


def _nome(user):
    if user is None:
        return None
    return user.first_name or user.email or user.username


def linha_da_empresa(conta, agora=None):
    agora = agora or timezone.now()
    leitura = leitura_da_conta(conta, agora)
    assinatura = assinatura_da(conta)
    pendente = aviso_pendente(assinatura) if assinatura else None
    ultimo = (
        assinatura.pagamentos.filter(status=Pagamento.Status.CONFIRMADO)
        .order_by("-resolvido_em")
        .first()
        if assinatura
        else None
    )
    return {
        "id": str(conta.public_id),
        "nome": conta.nome,
        "ativo": conta.ativo,
        "situacao": leitura.situacao.value,
        "valor_mensal": str(assinatura.valor_mensal) if assinatura else None,
        "paga_ate": assinatura.paga_ate.isoformat() if assinatura else None,
        "isenta": assinatura.isenta if assinatura else True,
        "nunca_bloquear": assinatura.nunca_bloquear if assinatura else False,
        "aguardando": (
            {
                "id": str(pendente.public_id),
                "valor": str(pendente.valor),
                "informado_em": timezone.localtime(pendente.informado_em).isoformat(),
                "informado_por": _nome(pendente.informado_por),
            }
            if pendente
            else None
        ),
        "ultimo_pagamento": (
            {
                "valor": str(ultimo.valor),
                "resolvido_em": timezone.localtime(ultimo.resolvido_em).isoformat(),
                "origem": ultimo.origem,
            }
            if ultimo
            else None
        ),
    }


def painel_da_plataforma(agora=None):
    agora = agora or timezone.now()
    linhas = [
        linha_da_empresa(conta, agora)
        for conta in Conta.objects.select_related("assinatura").order_by("nome")
    ]
    linhas.sort(key=lambda linha: (ORDEM.get(linha["situacao"], 4), linha["nome"].lower()))
    contar = lambda situacao: sum(1 for linha in linhas if linha["situacao"] == situacao)  # noqa: E731
    return {
        "empresas": linhas,
        "contadores": {
            "aguardando": contar(Situacao.AGUARDANDO),
            "atrasadas": contar(Situacao.ATRASADA),
            "bloqueadas": contar(Situacao.BLOQUEADA),
        },
    }
