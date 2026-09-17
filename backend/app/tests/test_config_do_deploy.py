"""A leitura do banco a partir de DATABASE_URL.

O Railway (como Heroku, Render e Fly) entrega o banco numa variavel unica:
DATABASE_URL. O settings so sabia ler DB_ENGINE/DB_HOST/DB_USER separados, o
que obrigaria a picar a URL a mao em seis variaveis no painel — e um erro de
digitacao ali aparece como "banco nao conecta" no deploy, longe da causa.

Sem dependencia nova: a stdlib ja sabe partir uma URL, e o que falta e so o
mapa de esquema para engine do Django.
"""

from django.test import SimpleTestCase

from backend.banco import banco_da_url


class BancoDaUrlTests(SimpleTestCase):
    def test_mysql(self):
        config = banco_da_url("mysql://joao:segredo@hospedeiro.railway.app:3306/ferrovia")

        self.assertEqual(config["ENGINE"], "django.db.backends.mysql")
        self.assertEqual(config["NAME"], "ferrovia")
        self.assertEqual(config["USER"], "joao")
        self.assertEqual(config["PASSWORD"], "segredo")
        self.assertEqual(config["HOST"], "hospedeiro.railway.app")
        self.assertEqual(config["PORT"], "3306")

    def test_postgres_e_seus_apelidos(self):
        """O Railway usa postgresql://; o Heroku historicamente usa postgres://."""
        for esquema in ("postgres", "postgresql"):
            config = banco_da_url(f"{esquema}://u:p@h:5432/d")
            self.assertEqual(
                config["ENGINE"], "django.db.backends.postgresql", esquema
            )

    def test_senha_com_caractere_especial(self):
        """Senha gerada por provedor vem percent-encoded na URL.

        Sem decodificar, a senha chega literal com %40 e o banco recusa a
        conexao — com uma mensagem que nao diz que o problema e este.
        """
        config = banco_da_url("mysql://u:s%40lt%3Ao@h:3306/d")

        self.assertEqual(config["PASSWORD"], "s@lt:o")

    def test_url_sem_porta(self):
        config = banco_da_url("mysql://u:p@h/d")

        self.assertEqual(config["PORT"], "")

    def test_esquema_desconhecido_falha_alto(self):
        """Melhor estourar no boot do que subir apontando para lugar nenhum."""
        with self.assertRaises(ValueError):
            banco_da_url("mongodb://u:p@h:27017/d")

    def test_url_vazia_devolve_none(self):
        """Sem DATABASE_URL o settings segue pelo caminho antigo (DB_ENGINE)."""
        self.assertIsNone(banco_da_url(""))
        self.assertIsNone(banco_da_url(None))
