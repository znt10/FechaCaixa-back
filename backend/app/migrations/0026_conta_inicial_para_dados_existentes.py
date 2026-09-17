"""Coloca tudo que ja existia dentro de uma primeira conta.

Antes desta migracao o sistema atendia uma rede de lojas so; agora atende
varias, isoladas por Conta. Sem este passo as lojas, os responsaveis de
retirada e os usuarios ja cadastrados ficariam orfaos — e, como quem nao tem
conta nao enxerga nada, o pessoal perderia o acesso ao proprio dado no
deploy seguinte.
"""

from django.db import migrations

NOME_DA_CONTA_INICIAL = "Marina"


def criar_conta_inicial(apps, schema_editor):
    Conta = apps.get_model("app", "Conta")
    PerfilUsuario = apps.get_model("app", "PerfilUsuario")
    Loja = apps.get_model("app", "Loja")
    ResponsavelRetirada = apps.get_model("app", "ResponsavelRetirada")
    User = apps.get_model("auth", "User")

    orfaos = Loja.objects.filter(conta__isnull=True)
    responsaveis_orfaos = ResponsavelRetirada.objects.filter(conta__isnull=True)

    if not orfaos.exists() and not responsaveis_orfaos.exists():
        # Banco novo (deploy limpo): nao inventa conta, quem cria e o super
        # admin pelo /admin/.
        return

    conta, _ = Conta.objects.get_or_create(nome=NOME_DA_CONTA_INICIAL)

    orfaos.update(conta=conta)
    responsaveis_orfaos.update(conta=conta)

    # Quem ja tinha login vira membro dessa conta. Super admin fica de fora:
    # ele e dono da plataforma e enxerga todas as contas, nao uma.
    for user in User.objects.filter(is_superuser=False):
        PerfilUsuario.objects.get_or_create(user=user, defaults={"conta": conta})


def remover_conta_inicial(apps, schema_editor):
    Conta = apps.get_model("app", "Conta")
    Conta.objects.filter(nome=NOME_DA_CONTA_INICIAL).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0025_conta_fechamentocaixa_conferido_and_more"),
    ]

    operations = [
        migrations.RunPython(criar_conta_inicial, remover_conta_inicial),
    ]
