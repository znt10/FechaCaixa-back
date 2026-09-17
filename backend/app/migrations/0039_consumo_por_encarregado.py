"""O cadastro de quem lanca o caixa, e o consumo dele no turno.

Encarregado e a pessoa que toca a loja: lanca o fechamento e pode ter consumo
anotado. Nao e o "Funcionario" nem o "Gerente" do sistema, que sao cargos de
LOGIN — esta pessoa entra pelo codigo da empresa no aparelho.

O consumo mora no proprio fechamento, como a despesa e a retirada, e nao num
modelo proprio: e sempre de quem lancou o turno, entao guardar a pessoa de
novo seria repetir `lancado_por` — dado repetido e dado que diverge.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0038_ordenar_responsavel_retirada"),
    ]

    operations = [
        migrations.CreateModel(
            name="Encarregado",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False,
                                               unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("nome", models.CharField(max_length=100)),
                ("ativo", models.BooleanField(default=True)),
                ("conta", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="encarregados", to="app.conta")),
            ],
            options={"ordering": ["conta__nome", "nome"]},
        ),
        migrations.AddField(
            model_name="fechamentocaixa",
            name="lancado_por",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="fechamentos_lancados", to="app.encarregado"),
        ),
        migrations.AddField(
            model_name="fechamentocaixa",
            name="houve_consumo",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="fechamentocaixa",
            name="valor_consumo",
            field=models.DecimalField(blank=True, decimal_places=2,
                                      max_digits=10, null=True),
        ),
    ]
