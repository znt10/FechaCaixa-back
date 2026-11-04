from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

ANTES = [("app", "0063_merge_boletos_e_infinitepay")]
DEPOIS = [("app", "0064_comprovantes_pessoais")]


class MigracaoDaLeituraPorCaixaTests(TransactionTestCase):
    """A linha unica de hoje (pk=1) vira a da caixa das lojas sem perder o UID:
    perder o ponto de leitura faria o leitor recomecar do fim e pular e-mails."""

    def test_linha_existente_vira_lojas_com_o_mesmo_uid(self):
        executor = MigrationExecutor(connection)
        executor.migrate(ANTES)
        antigas = executor.loader.project_state(ANTES).apps
        antigas.get_model("app", "LeituraDaCaixa").objects.create(
            pk=1, uid_validade=7, ultimo_uid=99
        )

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(DEPOIS)
        novas = executor.loader.project_state(DEPOIS).apps
        leitura = novas.get_model("app", "LeituraDaCaixa").objects.get(caixa="LOJAS")
        self.assertEqual((leitura.uid_validade, leitura.ultimo_uid), (7, 99))

        # Deixa o banco no fim, como os outros testes esperam.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
