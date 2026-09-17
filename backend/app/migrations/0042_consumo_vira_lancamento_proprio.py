"""O consumo deixa de ser um campo do fechamento e vira uma linha por pessoa.

Ate aqui `houve_consumo` e `valor_consumo` moravam dentro do FechamentoCaixa,
e por isso cabia um consumo por turno, sempre da pessoa que lancou o caixa.
Numa empresa com 60 funcionarios e 20 lancando, as outras 40 nao tinham onde
aparecer — e e a soma dessas 60 que a dona desconta no fim do mes.

Quem registra continua sendo o gerente, no mesmo envio do fechamento; o que
muda e que agora ele diz de quem foi cada valor, quantas pessoas precisar.
O cadastro ganha as duas marcas que separam os papeis na tela da empresa:
quem fecha o caixa e quem aparece na lista de consumo.

A ordem das operacoes aqui importa e nao e a que o makemigrations gerou: ele
poe os RemoveField primeiro, o que apagaria os valores antes de terem para
onde ir. Cria-se a tabela, move-se o dado, e so entao os campos saem.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


def mover_consumos_para_a_tabela(apps, schema_editor):
    FechamentoCaixa = apps.get_model("app", "FechamentoCaixa")
    Consumo = apps.get_model("app", "Consumo")

    com_consumo = FechamentoCaixa.objects.filter(houve_consumo=True).exclude(
        valor_consumo=None
    )

    linhas = []
    sem_dono = 0
    for fechamento in com_consumo.iterator():
        if fechamento.valor_consumo <= 0:
            # Flag ligada e campo vazio e o meio do caminho do formulario, nao
            # um consumo.
            continue
        if not fechamento.lancado_por_id:
            # Anterior ao cadastro de encarregados: so existe o texto do nome,
            # e o consumo e de alguem. Inventar dono seria pior do que perder o
            # numero — a dona descontaria do salario da pessoa errada.
            sem_dono += 1
            continue
        linhas.append(
            Consumo(
                fechamento_id=fechamento.id,
                encarregado_id=fechamento.lancado_por_id,
                valor=fechamento.valor_consumo,
            )
        )

    Consumo.objects.bulk_create(linhas)
    print(f"  consumos movidos: {len(linhas)}")
    if sem_dono:
        print(f"  IGNORADOS por nao ter quem lancou: {sem_dono}")


def devolver_consumos_para_o_fechamento(apps, schema_editor):
    """Caminho de volta para quem precisar reverter o deploy.

    Cabia um consumo por fechamento, que era o limite do desenho antigo: se o
    turno tiver duas pessoas, os dois valores voltam somados num campo so.
    Nada se perde em dinheiro, mas as linhas nao voltam identicas — e o nome
    de quem consumiu se perde, porque no desenho antigo ele nao existia.
    """
    Consumo = apps.get_model("app", "Consumo")

    for consumo in Consumo.objects.select_related("fechamento").iterator():
        fechamento = consumo.fechamento
        fechamento.houve_consumo = True
        fechamento.valor_consumo = (fechamento.valor_consumo or 0) + consumo.valor
        fechamento.save(update_fields=["houve_consumo", "valor_consumo"])


class Migration(migrations.Migration):

    dependencies = [("app", "0041_domingo_vira_turno_proprio")]

    operations = [
        migrations.AddField(
            model_name="encarregado",
            name="pode_lancar_caixa",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="encarregado",
            name="pode_consumir",
            field=models.BooleanField(default=True),
        ),
        migrations.CreateModel(
            name="Consumo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("valor", models.DecimalField(decimal_places=2, max_digits=10)),
                ("encarregado", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="consumos", to="app.encarregado")),
                ("fechamento", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="consumos", to="app.fechamentocaixa")),
            ],
            options={"ordering": ["-fechamento__data", "encarregado__nome"]},
        ),
        migrations.RunPython(
            mover_consumos_para_a_tabela, devolver_consumos_para_o_fechamento
        ),
        migrations.RemoveField(model_name="fechamentocaixa", name="houve_consumo"),
        migrations.RemoveField(model_name="fechamentocaixa", name="valor_consumo"),
    ]
