"""Sai o Unistock: estoque, pedidos, produtos e notificacoes in-app.

O projeto nasceu como Unistock (controle de estoque e pedidos) e virou
FechaCaixa. As duas coisas conviveram no mesmo banco; esta migracao encerra a
convivencia.

ISTO APAGA TABELAS. Nao ha volta sem backup: o reverse do Django recriaria as
tabelas vazias, nao os dados. Quando ela foi escrita o banco tinha 8
categorias e mais nada em todas as outras — se o seu tiver conteudo, tire um
dump antes de migrar.

Fica: Conta, PerfilUsuario, Loja, ResponsavelRetirada, FechamentoCaixa,
DispositivoDoFormulario e Feriado.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0035_periodo_dia'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='categoria',
            name='categoria_nome_unico_por_gerente',
        ),
        migrations.RemoveField(
            model_name='produto',
            name='categoria',
        ),
        migrations.RemoveConstraint(
            model_name='estoque',
            name='estoque_unico_por_produto_loja',
        ),
        migrations.RemoveConstraint(
            model_name='estoque',
            name='estoque_nao_negativo',
        ),
        migrations.RemoveField(
            model_name='notificacao',
            name='estoque',
        ),
        migrations.RemoveField(
            model_name='pedido',
            name='produtos',
        ),
        migrations.RemoveField(
            model_name='movimentacaoestoque',
            name='loja_destino',
        ),
        migrations.RemoveField(
            model_name='movimentacaoestoque',
            name='loja_origem',
        ),
        migrations.RemoveField(
            model_name='movimentacaoestoque',
            name='produto',
        ),
        migrations.RemoveField(
            model_name='movimentacaoestoque',
            name='usuario',
        ),
        migrations.RemoveField(
            model_name='notificacao',
            name='loja',
        ),
        migrations.RemoveField(
            model_name='notificacao',
            name='pedido',
        ),
        migrations.RemoveField(
            model_name='notificacao',
            name='usuario',
        ),
        migrations.RemoveField(
            model_name='pedido',
            name='loja',
        ),
        migrations.RemoveField(
            model_name='pedido',
            name='responsavel',
        ),
        migrations.RemoveField(
            model_name='preferencianotificacao',
            name='usuario',
        ),
        migrations.RemoveField(
            model_name='produto',
            name='gerente',
        ),
        migrations.RemoveField(
            model_name='categoria',
            name='gerente',
        ),
        migrations.RemoveField(
            model_name='estoque',
            name='loja',
        ),
        migrations.RemoveField(
            model_name='estoque',
            name='produto',
        ),
        migrations.DeleteModel(
            name='ItemPedido',
        ),
        migrations.DeleteModel(
            name='MovimentacaoEstoque',
        ),
        migrations.DeleteModel(
            name='Notificacao',
        ),
        migrations.DeleteModel(
            name='Pedido',
        ),
        migrations.DeleteModel(
            name='PreferenciaNotificacao',
        ),
        migrations.DeleteModel(
            name='Categoria',
        ),
        migrations.DeleteModel(
            name='Estoque',
        ),
        migrations.DeleteModel(
            name='Produto',
        ),
    ]
