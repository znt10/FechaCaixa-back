"""Regras de acesso do FechaCaixa — fonte unica.

Quem pode o que se decide aqui, e em mais lugar nenhum. As classes DRF sao so
a interface para o framework: a regra em si sao as funcoes de modulo, para que
codigo fora de request (tasks do Celery, comandos, o bot) faca a mesma pergunta
sem inventar a propria resposta.

Antes disso a mesma regra vivia em tres copias (aqui, em api/v1/viewsets.py e
em notifications/__init__.py) e o papel do usuario em duas (aqui e em
views.py). Tres lugares para mudar quando a regra mudasse, e nada garantindo
que os tres mudassem juntos.
"""

from rest_framework.permissions import BasePermission, SAFE_METHODS

from app.grupos import GRUPO_ADMIN, GRUPO_FUNCIONARIO, GRUPO_GERENTE

# Quem enxerga todas as lojas.
GRUPOS_GERENCIA = (GRUPO_ADMIN, GRUPO_GERENTE)


def is_admin(user):
    """True so para Admin de verdade (superuser ou grupo Admin) — sem Gerente.

    Diferenca de is_gerente_ou_admin: esta e para os pontos onde Gerente NAO
    pode agir como admin (criar outro gerente, ver todos os usuarios).
    """
    if not user or not user.is_authenticated:
        return False

    return user.is_superuser or user.groups.filter(name=GRUPO_ADMIN).exists()


def is_gerente(user):
    """True se o usuario e Gerente (independente de ter lojas atribuidas)."""
    if not user or not user.is_authenticated:
        return False

    return user.groups.filter(name=GRUPO_GERENTE).exists()


def is_gerente_ou_admin(user):
    """True se o usuario tem visao de todas as lojas.

    Aceita None e AnonymousUser: tambem e chamada fora de request (digest das
    7h), onde nao ha garantia de existir usuario.

    NAO olha is_active de proposito: quem barra conta desativada e o login.
    Mudar isso e decisao de produto, nao detalhe de implementacao.
    """
    return is_admin(user) or is_gerente(user)


def get_conta_do_usuario(user):
    """A conta do usuario logado, ou None para quem ve tudo/nada.

    None tem dois significados opostos de proposito, e quem chama precisa
    saber a diferenca: super admin (dono da plataforma) enxerga todas as
    contas; qualquer outro usuario sem perfil nao enxerga nenhuma. Por isso
    todo get_queryset checa is_superuser ANTES de chamar esta funcao.
    """
    if not user or not user.is_authenticated or user.is_superuser:
        return None

    perfil = getattr(user, "perfil", None)
    return perfil.conta if perfil else None


def is_funcionario(user):
    """True se o usuario e o login de conferencia da empresa.

    Cuidado com o nome: em varios comentarios deste projeto "funcionario" e a
    pessoa que preenche o formulario na loja, e essa NAO tem login — entra
    pelo codigo da empresa no aparelho. Aqui, funcionario e o cargo: ve o
    painel, confere e corrige lancamento, e nao administra a empresa.
    """
    if not user or not user.is_authenticated:
        return False

    return user.groups.filter(name=GRUPO_FUNCIONARIO).exists()


def pode_conferir_o_caixa(user):
    """Quem trabalha o caixa no painel: gerencia + funcionario.

    Separada de is_gerente_ou_admin porque as duas respondem perguntas
    diferentes: esta e "pode olhar e mexer no caixa lancado", a outra e "pode
    decidir sobre a empresa". Sao o mesmo conjunto de pessoas hoje menos o
    funcionario — e e exatamente essa diferenca que da o cargo dele.
    """
    return is_gerente_ou_admin(user) or is_funcionario(user)


def get_user_group_name(user):
    """Papel do usuario para a interface: Admin, Gerente, Responsavel ou None.

    Admin ganha de qualquer outro grupo. Para os demais o desempate e
    `groups.first()` SEM order_by, ou seja: quem esta em dois grupos recebe um
    papel que depende da ordem que o banco devolver. Comportamento antigo,
    mantido de proposito aqui — trocar por prioridade explicita muda o que a
    tela mostra para essas pessoas e precisa ser decidido, nao herdado.
    """
    if user.is_superuser or user.groups.filter(name=GRUPO_ADMIN).exists():
        return GRUPO_ADMIN

    group = user.groups.first()
    return group.name if group else None


class IsGerenteOrAdministrador(BasePermission):
    """Rotas exclusivas da gerencia (ex: relatorio global de pedidos)."""

    def has_permission(self, request, view):
        return is_gerente_ou_admin(request.user)


class PodeConferirOCaixa(BasePermission):
    """O painel de caixa: gerencia e funcionario.

    O isolamento por empresa NAO mora aqui — vive no get_queryset de cada
    viewset, que filtra por loja__conta. As duas pontas precisam ser lidas
    juntas: esta classe diz quem entra, o queryset diz o que a pessoa ve.
    """

    def has_permission(self, request, view):
        return pode_conferir_o_caixa(request.user)


class PodeAdministrarALoja(BasePermission):
    """Editar e apagar uma loja: a gerencia da empresa dela.

    Substituiu IsGerenteOrAdministradorOrResponsavel, que exigia o Gerente ser
    o `Loja.gerente` para escrever. Aquela regra era do Unistock, onde varios
    gerentes dividiam as lojas de uma rede; aqui a gerente e a dona da operacao
    da propria empresa — e as lojas que ja existiam nasceram sem gerente
    atribuido, ou seja, a regra antiga a trancava para fora das proprias lojas.
    O botao "Desativar" da tela da empresa respondia 403 em todas elas.
    """

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        if request.method in SAFE_METHODS:
            return True

        return is_gerente_ou_admin(user)

    def has_object_permission(self, request, view, loja):
        user = request.user

        if request.method in SAFE_METHODS:
            return True

        if is_admin(user):
            return True

        if is_gerente(user):
            conta = get_conta_do_usuario(user)
            return conta is not None and loja.conta_id == conta.id

        return False


class TemAcessoAoFormulario(BasePermission):
    """O aparelho ja digitou o codigo da empresa.

    Injeta request.conta_do_formulario. E de la que a conta sai daqui para a
    frente — o ?conta= da URL nao vale mais para o formulario, porque ele e
    escolhido por quem chama, e era o que deixava ler as lojas de qualquer
    empresa.
    """

    message = "Digite o codigo da empresa para lancar o caixa."

    def has_permission(self, request, view):
        from app.models import DispositivoDoFormulario

        token = request.COOKIES.get("formulario_token")
        dispositivo = DispositivoDoFormulario.objects.ativo_por_token(token)
        if dispositivo is None:
            return False

        dispositivo.registrar_uso()
        request.dispositivo_do_formulario = dispositivo
        request.conta_do_formulario = dispositivo.conta
        return True


class IsAdministrador(BasePermission):
    """So Admin — Gerente nao entra.

    Sem uso no momento, e de proposito: a tela de Empresa (codigo de acesso,
    aparelhos, quem retira dinheiro) passou para o Gerente, e a tela do Admin
    ainda vai ser definida. Esta classe e o lugar onde ela vai se apoiar — o
    que Admin podera fazer e Gerente nao.
    """

    message = "Apenas o administrador da empresa pode fazer isso."

    def has_permission(self, request, view):
        return is_admin(request.user)


class ModuloDeNotasAtivo(BasePermission):
    """Barra quem nao tem o modulo de nota fiscal ligado na conta.

    Fica ao lado das outras regras e nao dentro da view porque este arquivo e
    a fonte unica de quem pode o que — foi a copia da mesma regra em tres
    lugares que motivou centralizar aqui.
    """

    message = "O modulo de notas fiscais nao esta ativo para esta empresa."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True

        conta = get_conta_do_usuario(user)
        return bool(conta and conta.modulo_notas_ativo)
