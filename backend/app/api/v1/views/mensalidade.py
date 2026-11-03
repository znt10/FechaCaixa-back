"""A mensalidade vista pela propria empresa: o Pix e o "Ja paguei", ou o link
da InfinitePay.

As rotas de /minha-mensalidade/ ficam livres na trava do painel (ROTAS_LIVRES_NO_BLOQUEIO):
e por elas que a empresa bloqueada paga e se destrava.
"""

import logging

from django.utils import timezone
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from app.permissions import get_conta_do_usuario, is_gerente_ou_admin
from app.services.mensalidade import (
    RecusaDaMensalidade,
    Situacao,
    informar_pagamento,
    link_de_pagamento,
)
from app.services import infinitepay
from app.services.infinitepay import ErroDaInfinitePay
from app.services.mensalidade import infinitepay_retorno
from app.services.mensalidade.retrato import retrato_da_mensalidade

logger = logging.getLogger(__name__)


class MinhaMensalidadeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        conta = get_conta_do_usuario(request.user)
        # Superuser (sem empresa de proposito) e login sem empresa: nao ha
        # mensalidade a mostrar, e a tela nao desenha faixa nenhuma.
        if conta is None:
            return Response({"situacao": Situacao.ISENTA.value})
        return Response(retrato_da_mensalidade(conta, request.user))


class _AcaoDoGerente(APIView):
    """Um POST da empresa sobre a propria mensalidade, so do gerente."""

    permission_classes = [IsAuthenticated]
    recusa_de_quem_nao_e_gerente = ""

    def acao(self, conta, usuario):
        raise NotImplementedError

    def post(self, request):
        conta = get_conta_do_usuario(request.user)
        if conta is None:
            return Response(
                {"error": "Este login não está ligado a nenhuma empresa."}, status=400
            )
        if not is_gerente_ou_admin(request.user):
            return Response({"error": self.recusa_de_quem_nao_e_gerente}, status=403)
        try:
            self.acao(conta, request.user)
        except RecusaDaMensalidade as recusa:
            return Response({"error": recusa.mensagem}, status=400)
        return Response(retrato_da_mensalidade(conta, request.user), status=201)


class JaPagueiView(_AcaoDoGerente):
    recusa_de_quem_nao_e_gerente = "Peça ao gerente para informar o pagamento."

    def acao(self, conta, usuario):
        informar_pagamento(conta, usuario)


class LinkDePagamentoView(_AcaoDoGerente):
    """O "Pagar mensalidade": devolve o retrato ja com o `link` da InfinitePay."""

    recusa_de_quem_nao_e_gerente = "Peça ao gerente para pagar a mensalidade."

    def acao(self, conta, usuario):
        link_de_pagamento(conta, usuario)


class InfinitePayRetornoView(APIView):
    """POST /api/v1/infinitepay/retorno/ — a InfinitePay avisando que um link
    da mensalidade foi pago.

    Chega pelo rewrite /backend do front (a `webhook_url` e montada assim em
    `infinitepay.criar_link`), sem cookie e sem login: nao passa pela trava
    da mensalidade, e e assim que a empresa bloqueada se destrava sozinha.

    O aviso nao e assinado: a credencial e o `t` da query string, e mesmo com
    ele certo nada se confirma sem o `payment_check` (ver
    `mensalidade/infinitepay_retorno.py`). Responde 200 para o que nao vai
    mudar nunca (corpo estranho, pagamento desconhecido) e 400 quando a
    InfinitePay nao respondeu ao `payment_check`: e com 400 que ela manda o
    aviso de novo.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    def post(self, request):
        if not infinitepay.segredo_confere(request.query_params.get("t", "")):
            return Response({"error": "não autorizado"}, status=401)
        corpo = request.data if isinstance(request.data, dict) else {}
        referencia = str(corpo.get("order_nsu") or "")
        if not referencia:
            return Response({"ok": True, "resultado": "ignorada"})
        try:
            resultado = infinitepay_retorno.processar(
                referencia=referencia,
                transacao=str(corpo.get("transaction_nsu") or ""),
                slug=str(corpo.get("invoice_slug") or ""),
                agora=timezone.now(),
            )
        except ErroDaInfinitePay:
            logger.exception("[infinitepay] payment_check falhou (order_nsu=%s)", referencia)
            return Response({"error": "tente de novo"}, status=400)
        return Response({"ok": True, "resultado": resultado})
