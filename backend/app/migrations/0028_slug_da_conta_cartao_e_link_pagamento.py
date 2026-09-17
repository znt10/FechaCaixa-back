"""Apelido da conta e as novas formas de pagamento.

Escrita a mao porque a ordem importa: os campos novos precisam existir antes
da 0029 somar credito+debito dentro de cartao, e credito/debito so podem sair
depois disso (0030).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0027_conta_obrigatoria"),
    ]

    operations = [
        migrations.AddField(
            model_name="conta",
            name="slug",
            field=models.SlugField(max_length=60, null=True, blank=True, unique=True),
        ),
        migrations.AddField(
            model_name="fechamentocaixa",
            name="cartao",
            field=models.DecimalField(
                max_digits=10, decimal_places=2, null=True, blank=True, default=0
            ),
        ),
        migrations.AddField(
            model_name="fechamentocaixa",
            name="link_pagamento",
            field=models.DecimalField(
                max_digits=10, decimal_places=2, null=True, blank=True, default=0
            ),
        ),
    ]
