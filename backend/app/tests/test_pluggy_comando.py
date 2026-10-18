from io import StringIO
from types import SimpleNamespace
from unittest import mock

from cryptography.fernet import Fernet
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

ALVO = "app.management.commands.buscar_extratos_da_pluggy"


def _rodar():
    saida = StringIO()
    call_command("buscar_extratos_da_pluggy", stdout=saida)
    return saida.getvalue()


@override_settings(PLUGGY_CHAVE_CIFRA=Fernet.generate_key().decode())
class ComandoBuscarExtratosTests(SimpleTestCase):
    @override_settings(PLUGGY_CHAVE_CIFRA="")
    def test_sem_cifra_configurada_avisa_e_nao_busca(self):
        with mock.patch(f"{ALVO}.buscar_todas") as buscar:
            with self.assertLogs(ALVO, level="WARNING"):
                texto = _rodar()
        self.assertIn("PLUGGY_CHAVE_CIFRA vazia.", texto)
        buscar.assert_not_called()

    def test_uma_volta_resume_o_que_entrou(self):
        resultados = [SimpleNamespace(novas=3, adotadas=2)]
        with mock.patch(f"{ALVO}.buscar_todas", return_value=resultados):
            texto = _rodar()
        self.assertIn("1 conexões, 3 novas, 2 adotadas.", texto)

    def test_excecao_numa_volta_nao_derruba_o_comando(self):
        with mock.patch(f"{ALVO}.buscar_todas", side_effect=RuntimeError("boom")):
            with self.assertLogs(ALVO, level="ERROR"):
                _rodar()
