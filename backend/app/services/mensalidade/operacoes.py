"""O que se faz com a mensalidade: ler, avisar que pagou, confirmar, recusar
e registrar na mao. Nao conhece HTTP; as views e a tela Plataforma chamam
aqui."""

import logging
import uuid

from django.db import transaction
from django.utils import timezone

from app.excecoes import MENSAGEM_EMPRESA_DESATIVADA
from app.models import Assinatura, Pagamento
from app.services import infinitepay
from app.services.infinitepay import ErroDaInfinitePay

from .avisos import avisar_confirmacao, avisar_dono, avisar_recusa
from .datas import dia_de_vencimento_para
from .situacao import Situacao, ler_situacao, novo_paga_ate

logger = logging.getLogger(__name__)

SEM_MENSALIDADE = "Esta empresa não tem mensalidade a pagar."
AUTOMATICA = "A confirmação deste pagamento é automática."
LINK_FALHOU = (
    "Não foi possível gerar o link de pagamento agora. "
    "Tente de novo em alguns minutos."
)


class RecusaDaMensalidade(Exception):
    """A operacao nao pode acontecer. A mensagem vai para a tela."""

    def __init__(self, mensagem):
        self.mensagem = mensagem
        super().__init__(mensagem)


def assinatura_da(conta):
    try:
        return conta.assinatura
    except Assinatura.DoesNotExist:
        return None


def aviso_pendente(assinatura):
    return (
        assinatura.pagamentos.filter(status=Pagamento.Status.AGUARDANDO)
        .order_by("-informado_em")
        .first()
    )


def leitura_da_conta(conta, agora=None, *, considerar_aviso=True):
    agora = agora or timezone.now()
    hoje = timezone.localdate(agora)
    assinatura = assinatura_da(conta)
    if assinatura is None:
        # So acontece com dado torto (o save e a migration sempre criam).
        # Falta de dado nao cobra nem tranca ninguem: vale como isenta.
        return ler_situacao(
            conta_ativa=conta.ativo, isenta=True, nunca_bloquear=False,
            paga_ate=hoje, aviso_em=None, hoje=hoje, agora=agora,
        )
    pendente = aviso_pendente(assinatura) if considerar_aviso else None
    return ler_situacao(
        conta_ativa=conta.ativo,
        isenta=assinatura.isenta,
        nunca_bloquear=assinatura.nunca_bloquear,
        paga_ate=assinatura.paga_ate,
        aviso_em=pendente.informado_em if pendente else None,
        hoje=hoje,
        agora=agora,
    )


@transaction.atomic
def informar_pagamento(conta, usuario, agora=None):
    """O "Ja paguei": um aviso por vez, e o e-mail ao dono."""
    agora = agora or timezone.now()
    if not conta.ativo:
        raise RecusaDaMensalidade(MENSAGEM_EMPRESA_DESATIVADA)
    # A trava na linha da assinatura e o que impede dois avisos no duplo
    # clique: o segundo pedido espera o primeiro e ja encontra o pendente.
    assinatura = Assinatura.objects.select_for_update().filter(conta=conta).first()
    if assinatura is None or assinatura.isenta:
        raise RecusaDaMensalidade(SEM_MENSALIDADE)
    # Com a InfinitePay ligada quem confirma e o aviso dela; o "Ja paguei" so
    # seguraria o painel em AGUARDANDO esperando o dono conferir.
    if infinitepay.configurada():
        raise RecusaDaMensalidade(AUTOMATICA)
    if aviso_pendente(assinatura):
        raise RecusaDaMensalidade(
            "Você já avisou; o pagamento está aguardando confirmação."
        )
    pagamento = Pagamento.objects.create(
        assinatura=assinatura,
        status=Pagamento.Status.AGUARDANDO,
        origem=Pagamento.Origem.JA_PAGUEI,
        valor=assinatura.valor_mensal,
        informado_por=usuario,
        informado_em=agora,
    )
    avisar_dono(pagamento)
    return pagamento


def _travar_aguardando(pagamento):
    pagamento = (
        Pagamento.objects.select_for_update()
        .select_related("assinatura__conta")
        .get(pk=pagamento.pk)
    )
    if pagamento.status != Pagamento.Status.AGUARDANDO:
        raise RecusaDaMensalidade("Este pagamento já foi resolvido.")
    return pagamento


@transaction.atomic
def confirmar_pagamento(pagamento, usuario, agora=None):
    agora = agora or timezone.now()
    return _aplicar_confirmacao(_travar_aguardando(pagamento), usuario, agora)


@transaction.atomic
def confirmar_pelo_link(public_id, agora=None):
    """O aviso da InfinitePay ja conferido pelo `payment_check`: a mesma
    confirmacao do dono, sem ninguem no `resolvido_por`."""
    agora = agora or timezone.now()
    pagamento = (
        Pagamento.objects.select_for_update()
        .select_related("assinatura__conta")
        .filter(public_id=public_id, origem=Pagamento.Origem.INFINITEPAY)
        .first()
    )
    if pagamento is None or pagamento.status != Pagamento.Status.ABERTO:
        raise RecusaDaMensalidade("Este pagamento já foi resolvido.")
    return _aplicar_confirmacao(pagamento, None, agora)


def _aplicar_confirmacao(pagamento, usuario, agora):
    """Marca pago e avanca o `paga_ate`. Chamar com o pagamento travado."""
    assinatura = pagamento.assinatura
    # Sem o aviso na conta: e justamente ele que faria a empresa parecer
    # AGUARDANDO, e a pergunta aqui e se ela estava bloqueada pelas datas.
    leitura = leitura_da_conta(assinatura.conta, agora, considerar_aviso=False)
    novo, dia = novo_paga_ate(
        paga_ate=assinatura.paga_ate,
        dia_de_vencimento=assinatura.dia_de_vencimento,
        estava_bloqueada=leitura.situacao == Situacao.BLOQUEADA,
        hoje=timezone.localdate(agora),
    )
    assinatura.paga_ate, assinatura.dia_de_vencimento = novo, dia
    assinatura.save(update_fields=["paga_ate", "dia_de_vencimento", "updated_at"])

    pagamento.status = Pagamento.Status.CONFIRMADO
    pagamento.resolvido_por = usuario
    pagamento.resolvido_em = agora
    pagamento.paga_ate_depois = novo
    pagamento.save(update_fields=[
        "status", "resolvido_por", "resolvido_em", "paga_ate_depois", "updated_at",
    ])
    cancelar_links_abertos(assinatura)
    avisar_confirmacao(pagamento)
    return pagamento


@transaction.atomic
def recusar_pagamento(pagamento, usuario, agora=None):
    agora = agora or timezone.now()
    pagamento = _travar_aguardando(pagamento)
    pagamento.status = Pagamento.Status.RECUSADO
    pagamento.resolvido_por = usuario
    pagamento.resolvido_em = agora
    pagamento.save(update_fields=["status", "resolvido_por", "resolvido_em", "updated_at"])
    avisar_recusa(pagamento)
    return pagamento


@transaction.atomic
def registrar_pagamento_manual(assinatura, usuario, *, paga_ate, valor, observacao="", agora=None):
    """Dinheiro, Pix sem aviso: o dono diz ate quando esta pago."""
    agora = agora or timezone.now()
    pagamento = Pagamento.objects.create(
        assinatura=assinatura,
        status=Pagamento.Status.CONFIRMADO,
        origem=Pagamento.Origem.MANUAL,
        valor=valor,
        informado_por=usuario,
        informado_em=agora,
        resolvido_por=usuario,
        resolvido_em=agora,
        paga_ate_depois=paga_ate,
        observacao=observacao,
    )
    assinatura.paga_ate = paga_ate
    assinatura.dia_de_vencimento = dia_de_vencimento_para(paga_ate)
    assinatura.save(update_fields=["paga_ate", "dia_de_vencimento", "updated_at"])
    cancelar_links_abertos(assinatura)
    return pagamento


def _links(assinatura):
    return assinatura.pagamentos.filter(
        status=Pagamento.Status.ABERTO, origem=Pagamento.Origem.INFINITEPAY
    )


def link_aberto(assinatura):
    """O link da InfinitePay que ainda vale: aberto e do valor de hoje."""
    return (
        _links(assinatura)
        .filter(valor=assinatura.valor_mensal)
        .order_by("-informado_em", "-id")
        .first()
    )


def cancelar_links_abertos(assinatura, *, exceto_valor=None):
    """O mes foi pago por outro caminho (ou o valor mudou): o link aberto
    perde a vez. Se ainda assim for pago, o aviso dele nao confirma nada
    (ver infinitepay_retorno)."""
    links = _links(assinatura)
    if exceto_valor is not None:
        links = links.exclude(valor=exceto_valor)
    links.update(status=Pagamento.Status.CANCELADO, updated_at=timezone.now())


@transaction.atomic
def link_de_pagamento(conta, usuario, agora=None):
    """O "Pagar mensalidade": o link aberto, ou um novo.

    A trava na assinatura e o que impede dois links no duplo clique. O link
    sai antes da linha: se a InfinitePay falhar, nao fica pagamento aberto
    sem como pagar. O `public_id` do pagamento e o `order_nsu`."""
    agora = agora or timezone.now()
    if not conta.ativo:
        raise RecusaDaMensalidade(MENSAGEM_EMPRESA_DESATIVADA)
    assinatura = Assinatura.objects.select_for_update().filter(conta=conta).first()
    if assinatura is None or assinatura.isenta:
        raise RecusaDaMensalidade(SEM_MENSALIDADE)
    if not infinitepay.configurada():
        raise RecusaDaMensalidade("O pagamento por link não está configurado.")
    aberto = link_aberto(assinatura)
    if aberto:
        return aberto
    cancelar_links_abertos(assinatura, exceto_valor=assinatura.valor_mensal)

    public_id = uuid.uuid4()
    try:
        link = infinitepay.criar_link(
            valor_centavos=int(assinatura.valor_mensal * 100),
            descricao=f"Mensalidade FechaCaixa - {conta.nome}",
            referencia=str(public_id),
        )
    except ErroDaInfinitePay:
        logger.exception("[mensalidade] link da InfinitePay nao saiu")
        raise RecusaDaMensalidade(LINK_FALHOU)
    return Pagamento.objects.create(
        public_id=public_id,
        assinatura=assinatura,
        status=Pagamento.Status.ABERTO,
        origem=Pagamento.Origem.INFINITEPAY,
        valor=assinatura.valor_mensal,
        informado_por=usuario,
        informado_em=agora,
        link_pagamento=link,
    )
