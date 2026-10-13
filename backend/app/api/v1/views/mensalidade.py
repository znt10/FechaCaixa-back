"""A mensalidade vista pela propria empresa: o Pix e o "Ja paguei".

As duas rotas ficam livres na trava do painel (ROTAS_LIVRES_NO_BLOQUEIO):
e por elas que a empresa bloqueada paga e se destrava.
"""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from app.permissions import get_conta_do_usuario, is_gerente_ou_admin
from app.services.mensalidade import RecusaDaMensalidade, Situacao, informar_pagamento
from app.services.mensalidade.retrato import retrato_da_mensalidade


class MinhaMensalidadeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        conta = get_conta_do_usuario(request.user)
        # Superuser (sem empresa de proposito) e login sem empresa: nao ha
        # mensalidade a mostrar, e a tela nao desenha faixa nenhuma.
        if conta is None:
            return Response({"situacao": Situacao.ISENTA.value})
        return Response(retrato_da_mensalidade(conta, request.user))


class JaPagueiView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        conta = get_conta_do_usuario(request.user)
        if conta is None:
            return Response(
                {"error": "Este login não está ligado a nenhuma empresa."}, status=400
            )
        if not is_gerente_ou_admin(request.user):
            return Response(
                {"error": "Peça ao gerente para informar o pagamento."}, status=403
            )
        try:
            informar_pagamento(conta, request.user)
        except RecusaDaMensalidade as recusa:
            return Response({"error": recusa.mensagem}, status=400)
        return Response(retrato_da_mensalidade(conta, request.user), status=201)
