"""O aviso de pago da InfinitePay, e a varredura que cobre aviso perdido.

O aviso nao e assinado: quem descobrir a URL (e o `?t=`) consegue mandar um
POST com o `order_nsu` de qualquer link aberto. Por isso nada se confirma
pelo que veio no corpo — so pelo que o `payment_check` responde, e so se o
`amount` dele for o valor do link.

Copiado do Marcai (app/services/mensalidade/infinitepay_retorno.py); aqui o
link e um `Pagamento` ABERTO, e o `order_nsu` e o `public_id` dele.
"""

import logging
import uuid

from django.db import transaction
from django.utils import timezone

from app.models import Pagamento
from app.services import infinitepay

from .avisos import avisar_pago_em_cancelado
from .operacoes import RecusaDaMensalidade, confirmar_pelo_link

logger = logging.getLogger(__name__)

PAGO_EM_CANCELADO = "pago na InfinitePay depois de cancelado"


def _da_infinitepay(referencia):
    try:
        public_id = uuid.UUID(str(referencia))
    except ValueError:
        return None
    return Pagamento.objects.filter(
        public_id=public_id, origem=Pagamento.Origem.INFINITEPAY
    ).first()


def _centavos(valor):
    return int(valor * 100)


def processar(*, referencia: str, transacao: str, slug: str, agora) -> str:
    """Devolve "confirmada", "ja_resolvida", "paga_em_cancelada", "ignorada"
    ou "pendente". `ErroDaInfinitePay` sobe: a rota responde 400 (a
    InfinitePay manda de novo) e a varredura passa para o proximo."""
    pagamento = _da_infinitepay(referencia)
    if pagamento is None:
        return "ignorada"
    # Guardado antes de perguntar: e o que deixa a varredura conferir depois
    # se o `payment_check` falhar agora ou ainda disser que nao pagou.
    if not pagamento.ip_transacao and transacao:
        Pagamento.objects.filter(pk=pagamento.pk, ip_transacao="").update(
            ip_transacao=transacao, ip_fatura=slug or "", updated_at=timezone.now(),
        )
    if pagamento.status == Pagamento.Status.CONFIRMADO:
        return "ja_resolvida"
    if (
        pagamento.status == Pagamento.Status.CANCELADO
        and pagamento.observacao == PAGO_EM_CANCELADO
    ):
        return "paga_em_cancelada"

    resposta = infinitepay.conferir(referencia=str(pagamento.public_id), transacao=transacao, slug=slug)
    if not resposta["pago"]:
        return "pendente"
    if resposta["valor_centavos"] != _centavos(pagamento.valor):
        logger.warning(
            "[infinitepay] valor nao bate (pagamento=%s, esperado=%s, pago=%s)",
            pagamento.public_id, _centavos(pagamento.valor), resposta["valor_centavos"],
        )
        return "ignorada"

    if pagamento.status == Pagamento.Status.ABERTO:
        try:
            confirmar_pelo_link(pagamento.public_id, agora=agora)
        except RecusaDaMensalidade:
            # Outro aviso (ou o dono) resolveu entre a leitura e a trava.
            return "ja_resolvida"
        return "confirmada"
    return _pago_em_cancelado(pagamento.pk)


def _pago_em_cancelado(pk) -> str:
    """Marcado na observacao, o aviso ao dono sai uma vez so."""
    with transaction.atomic():
        pagamento = (
            Pagamento.objects.select_for_update()
            .select_related("assinatura__conta")
            .get(pk=pk)
        )
        if pagamento.observacao == PAGO_EM_CANCELADO:
            return "paga_em_cancelada"
        pagamento.observacao = PAGO_EM_CANCELADO
        pagamento.save(update_fields=["observacao", "updated_at"])
    avisar_pago_em_cancelado(pagamento)
    return "paga_em_cancelada"


def conferir_pendentes() -> dict:
    """A varredura: link aberto que ja recebeu ao menos um aviso. O que nunca
    recebeu aviso nao tem como ser conferido — o `payment_check` pede o
    `transaction_nsu` e o `slug`, que so o aviso traz —, e fica para o
    "registrar pagamento" da Plataforma."""
    contagem = {}
    pendentes = (
        Pagamento.objects.filter(
            status=Pagamento.Status.ABERTO, origem=Pagamento.Origem.INFINITEPAY
        )
        .exclude(ip_transacao="")
        .values_list("public_id", "ip_transacao", "ip_fatura")
    )
    for public_id, transacao, fatura in list(pendentes):
        try:
            r = processar(
                referencia=str(public_id), transacao=transacao, slug=fatura, agora=timezone.now()
            )
        except Exception:
            logger.exception("[infinitepay] varredura falhou no pagamento %s", public_id)
            r = "erro"
        contagem[r] = contagem.get(r, 0) + 1
    return contagem
