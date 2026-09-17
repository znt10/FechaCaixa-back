"""Contas da plataforma — visiveis so para o dono dela (super admin).

Criar conta continua sendo trabalho manual no /admin/ (nao ha autoatendimento);
este endpoint existe para o super admin listar e editar o que ja criou.
"""

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from app.models import Conta
from ..serializers import ContaSerializer


class ContaViewSet(viewsets.ModelViewSet):
    queryset = Conta.objects.all().order_by("nome")
    serializer_class = ContaSerializer
    lookup_field = 'public_id'
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if not self.request.user.is_superuser:
            return Conta.objects.none()
        return super().get_queryset()
