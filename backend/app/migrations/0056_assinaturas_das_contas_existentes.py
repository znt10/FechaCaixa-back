"""As empresas que ja existiam entram isentas.

Assim o deploy nao muda nada para ninguem: o dono tira a isencao de cada uma
na tela Plataforma quando decidir comecar a cobrar.
"""

from django.conf import settings
from django.db import migrations
from django.utils import timezone


def criar_assinaturas(apps, schema_editor):
    Conta = apps.get_model("app", "Conta")
    Assinatura = apps.get_model("app", "Assinatura")
    hoje = timezone.localdate()
    for conta in Conta.objects.filter(assinatura__isnull=True):
        Assinatura.objects.create(
            conta=conta,
            valor_mensal=settings.MENSALIDADE_VALOR_PADRAO,
            dia_de_vencimento=min(hoje.day, 28),
            paga_ate=hoje,
            isenta=True,
        )


class Migration(migrations.Migration):
    dependencies = [("app", "0055_mensalidade")]

    operations = [migrations.RunPython(criar_assinaturas, migrations.RunPython.noop)]
