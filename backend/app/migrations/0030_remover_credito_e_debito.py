"""Apaga credito e debito, agora que a 0029 ja somou os dois em cartao."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0029_migrar_cartao_e_gerar_slugs"),
    ]

    operations = [
        migrations.RemoveField(model_name="fechamentocaixa", name="credito"),
        migrations.RemoveField(model_name="fechamentocaixa", name="debito"),
    ]
