"""Que dias a loja tem um turno so.

Domingo e feriado a loja abre mais tarde e fecha mais tarde: e um expediente
so, que nao e a manha nem a tarde de um dia comum. A regra vive aqui, num
lugar so, porque tres coisas dependem dela: o formulario (quais turnos
oferecer), a validacao do lancamento e — mais pra frente — a cobranca de quem
nao lancou.

Os feriados nacionais sao calculados, nao cadastrados: sao os mesmos todo ano
e ninguem ia lembrar de cadastrar 12 datas em cada conta, todo janeiro. Os
municipais (padroeira, aniversario da cidade) sao cadastrados por conta, no
model Feriado, porque nao ha como adivinhar a cidade de cada negocio.
"""

from datetime import date, timedelta

DOMINGO = 6

# Dia e mes fixos, iguais em qualquer ano.
FERIADOS_FIXOS = {
    (1, 1): "Confraternizacao Universal",
    (4, 21): "Tiradentes",
    (5, 1): "Dia do Trabalho",
    (9, 7): "Independencia",
    (10, 12): "Nossa Senhora Aparecida",
    (11, 2): "Finados",
    (11, 15): "Proclamacao da Republica",
    (11, 20): "Consciencia Negra",
    (12, 25): "Natal",
}


def domingo_de_pascoa(ano):
    """Algoritmo de Gauss/Meeus — a Pascoa move os feriados moveis do ano."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(ano, mes, dia + 1)


def feriados_nacionais(ano):
    """{data: nome} dos feriados nacionais do ano."""
    feriados = {
        date(ano, mes, dia): nome for (mes, dia), nome in FERIADOS_FIXOS.items()
    }

    pascoa = domingo_de_pascoa(ano)
    feriados[pascoa - timedelta(days=47)] = "Carnaval"
    feriados[pascoa - timedelta(days=2)] = "Sexta-feira Santa"
    feriados[pascoa + timedelta(days=60)] = "Corpus Christi"

    return feriados


def eh_domingo(dia):
    """Domingo pelo calendario.

    Existe separado de `motivo_de_turno_unico` porque o turno nao e o mesmo: o
    domingo tem turno proprio (acontece toda semana, e a gerencia compara um
    domingo com o outro), e o feriado cai no turno do dia inteiro.
    """
    return dia.weekday() == DOMINGO


def motivo_de_turno_unico(dia, conta=None):
    """Por que este dia tem um turno so — ou None se tem manha e tarde.

    Devolve o motivo em vez de um booleano porque a tela mostra ele para o
    funcionario ("Feriado: Natal"), e um True nao explicaria nada.
    """
    if dia.weekday() == DOMINGO:
        return "Domingo"

    nacional = feriados_nacionais(dia.year).get(dia)
    if nacional:
        return f"Feriado: {nacional}"

    if conta is not None:
        from app.models import Feriado

        local = Feriado.objects.filter(conta=conta, data=dia).first()
        if local:
            return f"Feriado: {local.descricao}"

    return None


def turnos_do_dia(dia, conta=None):
    """Os turnos que existem num dia, para uma empresa — e o motivo, quando e
    um so.

    Empresa que fecha o caixa uma vez por dia so tem o dia inteiro, e a regra
    de domingo/feriado nao se aplica a ela: essa regra tira manha e tarde, e
    ali nao existe tarde para tirar. Nas outras, domingo tem turno proprio e
    feriado cai no do dia inteiro.

    Mora aqui, e nao na view, porque duas coisas perguntam: o formulario
    (quais turnos oferecer) e a correcao do turno pela gerencia (para qual
    turno o lancamento pode ir).
    """
    from app.models import FechamentoCaixa

    Periodo = FechamentoCaixa.Periodo
    if conta is not None and conta.fechamentos_por_dia == 1:
        return [Periodo.DIA], None

    motivo = motivo_de_turno_unico(dia, conta)
    if motivo:
        return [Periodo.DOMINGO if eh_domingo(dia) else Periodo.DIA], motivo
    return [Periodo.MANHA, Periodo.TARDE], None
