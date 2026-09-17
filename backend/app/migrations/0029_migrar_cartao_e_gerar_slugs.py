"""Preenche os campos novos a partir do que ja existia.

O caixa antigo separava credito e debito; o novo pergunta so "cartao", porque
a loja fecha a maquininha por um total. A soma preserva o valor do dia — a
divisao entre credito e debito e que se perde, e e por isso que a 0030 (que
apaga as colunas) vem depois e separada.
"""

from django.db import migrations
from django.utils.text import slugify


def preencher(apps, schema_editor):
    Conta = apps.get_model("app", "Conta")
    FechamentoCaixa = apps.get_model("app", "FechamentoCaixa")

    usados = set()
    for conta in Conta.objects.filter(slug__isnull=True):
        base = slugify(conta.nome)[:55] or f"conta-{conta.pk}"
        slug = base
        sufixo = 2
        # Contas com o mesmo nome existem; o slug e unico e vai no link.
        while slug in usados or Conta.objects.filter(slug=slug).exists():
            slug = f"{base}-{sufixo}"
            sufixo += 1
        usados.add(slug)
        conta.slug = slug
        conta.save(update_fields=["slug"])

    for fechamento in FechamentoCaixa.objects.all().iterator():
        fechamento.cartao = (fechamento.credito or 0) + (fechamento.debito or 0)
        fechamento.save(update_fields=["cartao"])


def desfazer(apps, schema_editor):
    """Volta o total do cartao para credito; debito fica zerado.

    Nao da para reconstruir a divisao original — quem reverte precisa saber
    disso, e por isso a operacao nao e silenciosa.
    """
    FechamentoCaixa = apps.get_model("app", "FechamentoCaixa")
    for fechamento in FechamentoCaixa.objects.all().iterator():
        fechamento.credito = fechamento.cartao or 0
        fechamento.debito = 0
        fechamento.save(update_fields=["credito", "debito"])


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0028_slug_da_conta_cartao_e_link_pagamento"),
    ]

    operations = [
        migrations.RunPython(preencher, desfazer),
    ]
