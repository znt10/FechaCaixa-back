"""Junta os dois ramos que sairam do 0044.

O cancelamento (0045_alter_fechamentocaixa_options_and_more) nasceu na main
enquanto a nota fiscal (0045_loja_cnpj ate 0049) seguia na propria branch, e os
dois numeraram 0045. Nao ha operacao aqui: os ramos mexem em modelos
diferentes e nao se contradizem — esta migracao so devolve uma folha unica ao
grafo, que e o que o `migrate` exige.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0045_alter_fechamentocaixa_options_and_more"),
        ("app", "0049_conta_modulo_notas_ativo"),
    ]

    operations = []
