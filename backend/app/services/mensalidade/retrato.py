"""O que a empresa ve da propria mensalidade (GET /minha-mensalidade/)."""

from django.conf import settings
from django.utils import timezone

from app.permissions import is_gerente_ou_admin
from app.services import infinitepay

from .operacoes import assinatura_da, aviso_pendente, leitura_da_conta, link_aberto
from .pix import copia_e_cola, identificador_da_conta, qr_svg


def pix_da_assinatura(assinatura):
    if not settings.PIX_CHAVE or assinatura.isenta:
        return None
    texto = copia_e_cola(
        chave=settings.PIX_CHAVE,
        nome=settings.PIX_NOME_RECEBEDOR,
        cidade=settings.PIX_CIDADE,
        valor=assinatura.valor_mensal,
        identificador=identificador_da_conta(assinatura.conta.public_id),
    )
    return {"copia_e_cola": texto, "qr_svg": qr_svg(texto)}


def retrato_da_mensalidade(conta, usuario, agora=None):
    agora = agora or timezone.now()
    leitura = leitura_da_conta(conta, agora)
    assinatura = assinatura_da(conta)
    if assinatura is None:
        return {"situacao": leitura.situacao.value}

    pendente = aviso_pendente(assinatura)
    # Pela InfinitePay a tela so abre o link: sem QR, sem copia e cola e sem
    # "Ja paguei", porque o aviso dela confirma sozinho.
    automatico = infinitepay.configurada()
    pode_pagar = bool(conta.ativo and not assinatura.isenta and is_gerente_ou_admin(usuario))
    aberto = link_aberto(assinatura) if automatico else None
    return {
        "situacao": leitura.situacao.value,
        "valor": str(assinatura.valor_mensal),
        "paga_ate": assinatura.paga_ate.isoformat(),
        "dias": leitura.dias,
        "bloqueia_em": leitura.bloqueia_em.isoformat() if leitura.bloqueia_em else None,
        "aguardando": (
            {"informado_em": timezone.localtime(pendente.informado_em).isoformat()}
            if pendente
            else None
        ),
        "pode_informar": pode_pagar and pendente is None and not automatico,
        "pode_pagar": pode_pagar,
        "pagamento_automatico": automatico,
        "link": aberto.link_pagamento if aberto else None,
        "pix": None if automatico else pix_da_assinatura(assinatura),
    }
