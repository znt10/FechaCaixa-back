"""Domingo ganha turno proprio; feriado vai para o turno do dia inteiro.

Ate aqui os dois eram gravados como MANHA, porque MANHA era o unico turno que
a validacao aceitava nesses dias. A loja abre mais tarde e fecha mais tarde no
domingo: o painel mostrava "Manha" para um caixa que fechou a noite, e quem
filtrava por manha somava o domingo junto com as manhas da semana — a media
saia errada e o domingo, que e o dia de movimento diferente, nao tinha como
ser olhado sozinho.

Sao dois turnos e nao um porque as duas datas nao se parecem: domingo acontece
toda semana e a gerencia compara um com o outro, entao merece o proprio valor;
feriado sao doze datas no ano, cada uma com um nome diferente que nao caberia
num enum — vai para DIA, o turno do expediente inteiro que ja existia para quem
fecha o caixa uma vez por dia.

Esta migracao acerta o historico junto. Sem ela o mesmo domingo apareceria como
"Manha" ate o dia do deploy e como "Domingo" depois dele, e a serie de quem
filtra por turno se partiria no meio.

So mexe em empresa de dois fechamentos por dia: quem fecha uma vez ja gravava
DIA em todo dia, domingo inclusive, e continua assim — sem manha e tarde para
confundir, um turno so ja diz tudo o que ha para dizer.
"""

from django.db import migrations, models

# A regra vem do modulo de verdade, e nao de uma copia congelada aqui: a lista
# de feriados e calculada (nao cadastrada), entao uma copia so serviria para
# divergir da regra que o formulario aplica.
from app.feriados import eh_domingo, motivo_de_turno_unico


def separar_domingo_e_feriado(apps, schema_editor):
    FechamentoCaixa = apps.get_model("app", "FechamentoCaixa")

    manhas = FechamentoCaixa.objects.filter(
        periodo="MANHA", loja__conta__fechamentos_por_dia=2
    ).values_list("id", "data", "loja__conta_id")

    # A consulta de feriado local e por conta e por dia: agrupar antes evita
    # repetir a mesma pergunta uma vez por lancamento.
    motivos = {}
    domingos, feriados = [], []
    for pk, dia, conta_id in manhas:
        chave = (dia, conta_id)
        if chave not in motivos:
            motivos[chave] = motivo_de_turno_unico(dia, conta_id)
        if not motivos[chave]:
            continue
        (domingos if eh_domingo(dia) else feriados).append(pk)

    FechamentoCaixa.objects.filter(id__in=domingos).update(periodo="DOMINGO")
    FechamentoCaixa.objects.filter(id__in=feriados).update(periodo="DIA")


def voltar_para_manha(apps, schema_editor):
    """O caminho de volta so existe para quem precisar reverter o deploy.

    E seguro porque nesses dias nunca existiu tarde: a validacao antiga
    recusava, entao todo DOMINGO e todo DIA de uma empresa de dois turnos veio
    de um MANHA.
    """
    FechamentoCaixa = apps.get_model("app", "FechamentoCaixa")

    FechamentoCaixa.objects.filter(
        periodo__in=["DOMINGO", "DIA"], loja__conta__fechamentos_por_dia=2
    ).update(periodo="MANHA")


class Migration(migrations.Migration):

    dependencies = [("app", "0040_encarregados_a_partir_do_historico")]

    operations = [
        migrations.AlterField(
            model_name="fechamentocaixa",
            name="periodo",
            field=models.CharField(
                choices=[
                    ("MANHA", "Manha"),
                    ("TARDE", "Tarde"),
                    ("DOMINGO", "Domingo"),
                    ("DIA", "Dia"),
                ],
                max_length=10,
            ),
        ),
        migrations.RunPython(separar_domingo_e_feriado, voltar_para_manha),
    ]
