from pathlib import Path

import yaml
from django.test import SimpleTestCase

COMPOSE = Path(__file__).resolve().parents[3] / "docker-compose.prod.yml"


class ComposeDeProducaoTests(SimpleTestCase):
    """O compose lista as variaveis uma a uma: o Coolify so entrega ao container
    o que estiver nomeado aqui. Variavel cadastrada no Coolify e esquecida aqui
    chega vazia, sem erro nenhum."""

    def ambiente(self, servico):
        return yaml.safe_load(COMPOSE.read_text())["services"][servico]["environment"]

    def test_leitor_recebe_as_duas_caixas(self):
        ambiente = self.ambiente("comprovantes")
        for nome in (
            "COMPROVANTES_EMAIL",
            "COMPROVANTES_SENHA_APP",
            "COMPROVANTES_PESSOAL_EMAIL",
            "COMPROVANTES_PESSOAL_SENHA_APP",
        ):
            self.assertIn(nome, ambiente)

    def test_api_mostra_o_endereco_pessoal(self):
        self.assertIn("COMPROVANTES_PESSOAL_EMAIL", self.ambiente("api"))
