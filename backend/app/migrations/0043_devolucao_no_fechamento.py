"""A devolucao passa a ser perguntada no fechamento.

Quando o cliente pede o dinheiro de volta, o funcionario tira da gaveta. Ate
aqui isso nao tinha onde ser registrado, e a dona so descobria olhando a
diferenca.

Os dois campos sao anulaveis e comecam desligados: nenhum lancamento anterior
teve devolucao registrada, e "nao sei" nao pode virar "houve".

A devolucao NAO entra na conta do total, e isso e proposital — ver o docstring
de FechamentoCaixa.total. O dinheiro saiu da mesma gaveta que o funcionario
contou, entao ela ja se descontou sozinha.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("app", "0042_consumo_vira_lancamento_proprio")]

    operations = [
        migrations.AddField(
            model_name="fechamentocaixa",
            name="houve_devolucao",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="fechamentocaixa",
            name="devolucao_valor",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True
            ),
        ),
    ]
