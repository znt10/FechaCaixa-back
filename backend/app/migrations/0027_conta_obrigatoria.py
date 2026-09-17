"""Torna a conta obrigatoria em Loja e ResponsavelRetirada.

Escrita a mao porque o makemigrations pergunta um default para as linhas
antigas — a 0026 ja adotou todas elas, entao nao existe linha nula aqui.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0026_conta_inicial_para_dados_existentes"),
    ]

    operations = [
        migrations.AlterField(
            model_name="loja",
            name="conta",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="lojas",
                to="app.conta",
            ),
        ),
        migrations.AlterField(
            model_name="responsavelretirada",
            name="conta",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="responsaveis_retirada",
                to="app.conta",
            ),
        ),
    ]
