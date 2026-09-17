"""Endpoints da tela de Empresa: a empresa administrando a si mesma.

Ate agora isso so existia no /admin/ do Django, que e do dono da plataforma. A
conta aqui vem sempre do login — nunca de um parametro — para que administrar a
propria empresa nao vire um caminho para administrar a do vizinho.

Quem entra e a gerencia (Gerente ou Admin), nao so o Admin. Trocar o codigo e
derrubar um aparelho parecem decisao de dono, mas acontecem no chao da loja:
quem esta la quando o celular novo precisa entrar e o gerente.
"""

from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from app.grupos import GRUPO_FUNCIONARIO
from app.models import DispositivoDoFormulario
from app.permissions import IsGerenteOrAdministrador, get_conta_do_usuario

from ..serializers.empresa import (
    AparelhoSerializer,
    FuncionarioSerializer,
    MinhaEmpresaSerializer,
    NovoFuncionarioSerializer,
)


class BaseDaEmpresa(APIView):
    permission_classes = [IsAuthenticated, IsGerenteOrAdministrador]

    def conta_da_gerencia(self, request):
        conta = get_conta_do_usuario(request.user)
        if conta is None:
            # Superuser nao tem perfil: ele administra pelo /admin/ do Django,
            # que enxerga todas as contas. Aqui, sem conta, nao ha "minha
            # empresa" para responder.
            self.permission_denied(
                request, message="Este login nao pertence a uma empresa."
            )
        return conta


class MinhaEmpresaView(BaseDaEmpresa):
    def get(self, request):
        return Response(MinhaEmpresaSerializer(self.conta_da_gerencia(request)).data)

    def patch(self, request):
        conta = self.conta_da_gerencia(request)
        serializer = MinhaEmpresaSerializer(conta, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class NovoCodigoView(BaseDaEmpresa):
    def post(self, request):
        conta = self.conta_da_gerencia(request)
        conta.codigo_acesso = conta.gerar_codigo_acesso()
        conta.save(update_fields=["codigo_acesso"])

        # Derruba os aparelhos junto, e e por isso que o botao existe: se o
        # codigo vazou, continuar aceitando quem entrou com ele seria trocar a
        # fechadura deixando as copias antigas funcionando.
        conta.dispositivos.filter(revogado_em__isnull=True).update(
            revogado_em=timezone.now()
        )
        return Response(MinhaEmpresaSerializer(conta).data)


class AparelhosView(BaseDaEmpresa):
    def get(self, request):
        aparelhos = self.conta_da_gerencia(request).dispositivos.filter(
            revogado_em__isnull=True
        )
        return Response(AparelhoSerializer(aparelhos, many=True).data)


class DesconectarAparelhoView(BaseDaEmpresa):
    def post(self, request, public_id=None):
        conta = self.conta_da_gerencia(request)
        # Filtra pela conta no proprio lookup: um id de aparelho do vizinho tem
        # que dar 404, e nao "nao autorizado" — a existencia dele nao e assunto
        # de quem perguntou.
        aparelho = get_object_or_404(
            DispositivoDoFormulario, public_id=public_id, conta=conta
        )
        aparelho.revogado_em = timezone.now()
        aparelho.save(update_fields=["revogado_em"])
        return Response({"message": "Aparelho desconectado."})


class BaseDosFuncionarios(BaseDaEmpresa):
    """O conjunto de quem pode ser gerenciado como funcionario.

    Duas travas na mesma consulta, e as duas importam: a conta (nao se mexe no
    login do vizinho) e o grupo (a gerente nao se desativa, nem desativa outra
    gerente, por esta rota). Um id que exista mas nao passe nas duas responde
    404 — a existencia dele nao e assunto de quem perguntou.
    """

    def funcionarios_da_conta(self, request):
        return User.objects.filter(
            groups__name=GRUPO_FUNCIONARIO, perfil__conta=self.conta_da_gerencia(request)
        ).order_by("first_name", "email")


class FuncionariosView(BaseDosFuncionarios):
    def get(self, request):
        return Response(
            FuncionarioSerializer(self.funcionarios_da_conta(request), many=True).data
        )

    def post(self, request):
        conta = self.conta_da_gerencia(request)
        serializer = NovoFuncionarioSerializer(
            data=request.data, context={"conta": conta}
        )
        serializer.is_valid(raise_exception=True)
        funcionario = serializer.save()
        return Response(
            FuncionarioSerializer(funcionario).data, status=status.HTTP_201_CREATED
        )


class FuncionarioView(BaseDosFuncionarios):
    def patch(self, request, id=None):
        """Desativa e reativa. E o caminho normal para tirar alguem do ar.

        Desativar preserva o nome nos turnos que a pessoa ja conferiu — o
        historico continua dizendo quem olhou o quê.
        """
        funcionario = get_object_or_404(self.funcionarios_da_conta(request), id=id)

        ativo = request.data.get("ativo")
        if not isinstance(ativo, bool):
            return Response(
                {"ativo": ["Informe true ou false."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        funcionario.is_active = ativo
        funcionario.save(update_fields=["is_active"])
        return Response(FuncionarioSerializer(funcionario).data)

    def delete(self, request, id=None):
        """Apaga de vez.

        O caixa que a pessoa conferiu sobrevive: conferido_por e SET_NULL. O
        que se perde e o nome de quem conferiu, e e por isso que a tela pede
        confirmacao e oferece desativar primeiro.
        """
        funcionario = get_object_or_404(self.funcionarios_da_conta(request), id=id)
        funcionario.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
