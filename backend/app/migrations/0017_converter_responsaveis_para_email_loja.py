"""Convertia o login do responsavel da loja no e-mail da propria loja.

Virou no-op quando o login de loja foi removido do projeto (a loja passou a
entrar pelo codigo da empresa no aparelho, sem usuario nem senha). O helper
que ela importava — app/migracoes_loja_login.py — saiu junto, e uma migracao
nao pode importar modulo que nao existe: qualquer `migrate` estouraria aqui,
inclusive num banco novo que nunca teve responsavel nenhum.

O corpo foi esvaziado, e nao a migracao apagada: bancos que ja aplicaram esta
linha guardam o numero dela, e sumir com o arquivo quebraria o historico. Nos
que ja rodaram, o trabalho esta feito; nos novos, nao ha nada para converter,
porque os campos que ela mexia (Loja.responsavel, Loja.email) nao existem mais.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0016_remove_preferencianotificacao_digest_ativo_and_more"),
    ]

    operations = [
        migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop),
    ]
