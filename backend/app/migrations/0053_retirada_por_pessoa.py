"""A retirada deixa de ser um par de campos do fechamento e vira uma linha por pessoa.

Ate aqui cabia uma pessoa por turno em `responsavel_retirada`/`valor_retirado`.
Quando duas pessoas retiram no mesmo expediente, a segunda nao tinha onde
entrar.

Diferente da despesa (0044), os campos antigos NAO saem: viram o resumo das
linhas (a soma, e a primeira pessoa), porque o total do caixa, os graficos e
a planilha leem `valor_retirado` e a soma e exatamente o numero deles. A ida e
1-para-1 e nao perde nada: cada fechamento com retirada vira uma linha, com a
mesma pessoa e o mesmo valor.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


def mover_retiradas_para_a_tabela(apps, schema_editor):
    FechamentoCaixa = apps.get_model("app", "FechamentoCaixa")
    Retirada = apps.get_model("app", "Retirada")

    # _base_manager: o historico nao tem `objects`, e o cancelado tambem
    # precisa das linhas — ele volta a valer se alguem desfizer o cancelamento.
    com_retirada = FechamentoCaixa._base_manager.filter(
        houve_retirada=True, valor_retirado__gt=0
    )

    linhas = [
        Retirada(
            fechamento_id=fechamento.id,
            responsavel_id=fechamento.responsavel_retirada_id,
            valor=fechamento.valor_retirado,
        )
        for fechamento in com_retirada.iterator()
    ]
    Retirada.objects.bulk_create(linhas)
    print(f"  retiradas movidas: {len(linhas)}")


def apagar_as_linhas(apps, schema_editor):
    """O caminho de volta so apaga a tabela: o resumo no fechamento ja tem a
    soma e a primeira pessoa, que e tudo o que o desenho antigo guardava."""


class Migration(migrations.Migration):

    dependencies = [("app", "0052_formulario_configuravel")]

    operations = [
        migrations.CreateModel(
            name="Retirada",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("valor", models.DecimalField(decimal_places=2, max_digits=10)),
                ("fechamento", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="retiradas", to="app.fechamentocaixa")),
                ("responsavel", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="retiradas_lancadas", to="app.responsavelretirada")),
            ],
            options={"ordering": ["-fechamento__data", "id"]},
        ),
        migrations.RunPython(mover_retiradas_para_a_tabela, apagar_as_linhas),
    ]
