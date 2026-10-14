"""Em que pe esta a mensalidade de uma empresa.

Pura: recebe datas e flags, nao le banco nem relogio. E a unica fonte da
resposta — a autenticacao, a API da empresa e a tela Plataforma perguntam
aqui, e uma regra copiada em tres lugares ja divergiu neste projeto antes.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from .datas import dia_de_vencimento_para, mes_seguinte

CARENCIA = timedelta(days=5)
ESPERA_DA_CONFIRMACAO = timedelta(days=3)
ANTECEDENCIA_DO_AVISO = timedelta(days=5)


class Situacao(StrEnum):
    DESATIVADA = "DESATIVADA"
    ISENTA = "ISENTA"
    AGUARDANDO = "AGUARDANDO"
    EM_DIA = "EM_DIA"
    VENCENDO = "VENCENDO"
    ATRASADA = "ATRASADA"
    BLOQUEADA = "BLOQUEADA"


TRAVAM_O_PAINEL = frozenset({Situacao.DESATIVADA, Situacao.BLOQUEADA})


@dataclass(frozen=True)
class Leitura:
    situacao: Situacao
    # Faltando para vencer (VENCENDO, EM_DIA) ou de atraso (ATRASADA,
    # BLOQUEADA). None quando a pergunta nao se aplica.
    dias: int | None = None
    # O primeiro dia em que o painel trava. None para quem nunca trava.
    bloqueia_em: date | None = None


def ler_situacao(*, conta_ativa, isenta, nunca_bloquear, paga_ate, aviso_em, hoje, agora):
    """A situacao, na ordem da especificacao: a primeira regra que casar decide."""
    if not conta_ativa:
        return Leitura(Situacao.DESATIVADA)
    if isenta:
        return Leitura(Situacao.ISENTA)

    bloqueia_em = None if nunca_bloquear else paga_ate + CARENCIA + timedelta(days=1)

    # O cliente disse que pagou e o dono ainda nao conferiu: nao se tranca
    # quem pode estar em dia. Passados 3 dias, o aviso deixa de segurar.
    if aviso_em is not None and aviso_em >= agora - ESPERA_DA_CONFIRMACAO:
        return Leitura(Situacao.AGUARDANDO, bloqueia_em=bloqueia_em)

    faltam = (paga_ate - hoje).days
    if faltam > ANTECEDENCIA_DO_AVISO.days:
        return Leitura(Situacao.EM_DIA, dias=faltam, bloqueia_em=bloqueia_em)
    if faltam >= 0:
        return Leitura(Situacao.VENCENDO, dias=faltam, bloqueia_em=bloqueia_em)

    atraso = -faltam
    if atraso <= CARENCIA.days or nunca_bloquear:
        return Leitura(Situacao.ATRASADA, dias=atraso, bloqueia_em=bloqueia_em)
    return Leitura(Situacao.BLOQUEADA, dias=atraso, bloqueia_em=bloqueia_em)


def novo_paga_ate(*, paga_ate, dia_de_vencimento, estava_bloqueada, hoje):
    """O (paga_ate, dia_de_vencimento) depois de confirmar um mes pago.

    Em dia ou na carencia, o mes pago e o seguinte ao vencimento, e o dia se
    mantem. Bloqueada, recomeca de hoje: quem ficou sem o painel nao paga
    pelos dias em que nao usou.
    """
    if estava_bloqueada:
        dia = dia_de_vencimento_para(hoje)
        return mes_seguinte(hoje, dia), dia
    return mes_seguinte(paga_ate, dia_de_vencimento), dia_de_vencimento
