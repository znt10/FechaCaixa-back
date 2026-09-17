"""/healthz: a rota que o provedor consulta para saber se o servico esta vivo.

Ela existe porque o healthcheck estava apontado para /api/v1/lojas/, que
responde 401 sem login — quem checa le qualquer coisa fora da faixa 2xx como
"servico doente" e derruba o deploy. O erro so apareceria no
primeiro deploy, com a aplicacao subindo e sendo morta em seguida.

Ela consulta o banco de proposito: um processo que responde mas nao alcanca o
banco nao serve para nada, e e melhor o provedor saber disso e nao mandar
trafego.
"""

from unittest.mock import patch

from django.test import SimpleTestCase


class HealthzTests(SimpleTestCase):
    databases = {"default"}

    def test_responde_200_sem_login(self):
        resp = self.client.get("/healthz/")

        self.assertEqual(resp.status_code, 200)

    def test_diz_que_o_banco_respondeu(self):
        resp = self.client.get("/healthz/")

        self.assertEqual(resp.json(), {"status": "ok", "banco": "ok"})

    def test_banco_fora_do_ar_responde_503(self):
        """Sem isto o provedor manda trafego para um processo que nao serve."""
        with patch("app.views.connection") as conexao:
            conexao.cursor.side_effect = Exception("connection refused")

            resp = self.client.get("/healthz/")

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["banco"], "fora do ar")

    def test_nao_conta_o_que_ha_dentro(self):
        """Rota aberta: nao pode virar fonte de informacao sobre o sistema."""
        corpo = self.client.get("/healthz/").content.decode()

        for vazamento in ("version", "django", "sql", "host", "debug"):
            self.assertNotIn(vazamento, corpo.lower())
