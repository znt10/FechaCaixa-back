"""A despesa deixa de ser um campo do fechamento e vira uma linha por gasto.

Ate aqui `houve_despesa`, `despesa_descricao` e `despesa_valor` moravam dentro
do FechamentoCaixa, e por isso cabia uma despesa por turno. O expediente gasta
com gas, agua e remedio no mesmo dia, e as outras iam empilhadas no campo de
texto — onde nao somam e nao se separam. Quem sente e a filha do dono, que
lanca cada uma na contabilidade dela.

Mesma mudanca que o consumo sofreu na 0042, e pelo mesmo motivo. A diferenca e
que aqui a ida e 1-para-1 e nao perde nada: cada fechamento com despesa vira
exatamente uma linha, com a mesma descricao e o mesmo valor.

A ordem das operacoes importa e nao e a que o makemigrations gera: ele poe os
RemoveField primeiro, o que apagaria os valores antes de terem para onde ir.
Cria-se a tabela, move-se o dado, e so entao os campos saem.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


def mover_despesas_para_a_tabela(apps, schema_editor):
    FechamentoCaixa = apps.get_model("app", "FechamentoCaixa")
    Despesa = apps.get_model("app", "Despesa")

    com_despesa = FechamentoCaixa.objects.filter(houve_despesa=True).exclude(
        despesa_valor=None
    )

    linhas = []
    for fechamento in com_despesa.iterator():
        if fechamento.despesa_valor <= 0:
            # Flag ligada e campo vazio e o meio do caminho do formulario, nao
            # uma despesa.
            continue
        linhas.append(
            Despesa(
                fechamento_id=fechamento.id,
                # A descricao era opcional no desenho antigo. Um rotulo
                # generico e melhor do que uma linha em branco na planilha da
                # contabilidade — o valor, que e o que importa, esta certo.
                descricao=(fechamento.despesa_descricao or "").strip() or "Despesa",
                valor=fechamento.despesa_valor,
            )
        )

    Despesa.objects.bulk_create(linhas)
    print(f"  despesas movidas: {len(linhas)}")


def devolver_despesas_para_o_fechamento(apps, schema_editor):
    """Caminho de volta para quem precisar reverter o deploy.

    Cabia uma despesa por fechamento, que era o limite do desenho antigo: se o
    turno tiver tres, as tres voltam somadas num campo so, com as descricoes
    concatenadas. Nada se perde em dinheiro, mas as linhas nao voltam
    identicas — e nao ha como voltarem, porque o desenho antigo nao tem onde
    guardar tres.
    """
    Despesa = apps.get_model("app", "Despesa")

    for despesa in Despesa.objects.select_related("fechamento").iterator():
        fechamento = despesa.fechamento
        anterior = (fechamento.despesa_descricao or "").strip()
        fechamento.houve_despesa = True
        fechamento.despesa_descricao = (
            f"{anterior}, {despesa.descricao}" if anterior else despesa.descricao
        )[:200]
        fechamento.despesa_valor = (fechamento.despesa_valor or 0) + despesa.valor
        fechamento.save(
            update_fields=["houve_despesa", "despesa_descricao", "despesa_valor"]
        )


class Migration(migrations.Migration):

    dependencies = [("app", "0043_devolucao_no_fechamento")]

    operations = [
        migrations.CreateModel(
            name="Despesa",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("descricao", models.CharField(max_length=200)),
                ("valor", models.DecimalField(decimal_places=2, max_digits=10)),
                ("fechamento", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="despesas", to="app.fechamentocaixa")),
            ],
            options={"ordering": ["-fechamento__data", "descricao"]},
        ),
        migrations.RunPython(
            mover_despesas_para_a_tabela, devolver_despesas_para_o_fechamento
        ),
        migrations.RemoveField(model_name="fechamentocaixa", name="houve_despesa"),
        migrations.RemoveField(model_name="fechamentocaixa", name="despesa_descricao"),
        migrations.RemoveField(model_name="fechamentocaixa", name="despesa_valor"),
    ]
