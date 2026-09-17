"""Atalhos de criacao para os testes.

Existe por causa da camada de Conta: `conta` e obrigatorio em Loja, mas a
maioria dos testes nao tem nada a ver com multi-tenant — repetir a criacao de
uma conta em cada setUp so acrescentaria ruido. Quem esta testando isolamento
entre contas passa `conta=` explicitamente.
"""

from app.models import Conta, Loja

CONTA_PADRAO = "Conta de teste"

# Uma quarta-feira ja passada, sem feriado nacional: o dia que os testes usam
# quando nao estao testando a regra de domingo/feriado.
#
# Existe porque a suite dependia de "hoje". Ela passava de segunda a sabado e
# quebrava no domingo, quando manha e tarde deixam de existir — e quebrou de
# verdade, num domingo, com 16 testes vermelhos que nao tinham nada a ver com
# turno. Data no passado de proposito: data futura tem validacao propria, e
# uma data fixa no passado continua valendo em qualquer ano que a suite rode.
DIA_COMUM = "2026-08-05"


def turno_de_hoje(conta=None):
    """O turno que o formulario aceita hoje.

    So para o punhado de testes que precisa mandar o lancamento SEM data —
    justamente os que provam que a data vem do servidor. Os outros fixam
    DIA_COMUM e nao dependem do calendario.
    """
    from django.utils import timezone

    from app.feriados import eh_domingo, motivo_de_turno_unico

    hoje = timezone.localdate()
    if not motivo_de_turno_unico(hoje, conta):
        return "MANHA"
    return "DOMINGO" if eh_domingo(hoje) else "DIA"


def conta_padrao():
    """A conta usada por quem nao se importa com qual conta e."""
    conta, _ = Conta.objects.get_or_create(nome=CONTA_PADRAO)
    return conta


def criar_loja(**campos):
    campos.setdefault("conta", conta_padrao())
    return Loja.objects.create(**campos)


def vincular_conta(user, conta=None):
    """Liga um usuario a uma conta — o que o /admin/ faz ao criar o login dele.

    Sem isso o usuario e autenticado mas nao pertence a conta nenhuma, e nao
    enxerga loja nem fechamento: e a regra de isolamento funcionando, nao um
    bug do teste.
    """
    from app.models import PerfilUsuario

    conta = conta or conta_padrao()
    perfil, criado = PerfilUsuario.objects.get_or_create(
        user=user, defaults={"conta": conta}
    )
    if not criado and perfil.conta_id != conta.id:
        perfil.conta = conta
        perfil.save(update_fields=["conta"])
    return perfil


def lista(resp):
    """Os itens de uma resposta de listagem, paginada ou nao.

    Nem toda listagem pagina: os cadastros de uma conta (encarregados, quem
    retira dinheiro) devolvem a lista crua de proposito, porque a tela mostra
    todos. O idioma antigo era `resp.data.get("results", resp.data)`, que
    parece cobrir os dois casos mas quebra justamente no cru — `ReturnList`
    nao tem `.get`.
    """
    return resp.data["results"] if isinstance(resp.data, dict) else resp.data
