from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings

from app.services.pluggy.cifra import (
    CifraNaoConfigurada,
    cifra_configurada,
    cifrar,
    decifrar,
)


@override_settings(PLUGGY_CHAVE_CIFRA=Fernet.generate_key().decode())
class CifraTests(SimpleTestCase):
    def test_cifra_e_decifra(self):
        cifrado = cifrar("segredo-123")
        self.assertNotIn("segredo-123", cifrado)
        self.assertEqual(decifrar(cifrado), "segredo-123")
        self.assertTrue(cifra_configurada())

    @override_settings(PLUGGY_CHAVE_CIFRA="")
    def test_sem_chave_recusa_com_mensagem(self):
        self.assertFalse(cifra_configurada())
        with self.assertRaisesMessage(CifraNaoConfigurada, "PLUGGY_CHAVE_CIFRA"):
            cifrar("x")
        with self.assertRaisesMessage(CifraNaoConfigurada, "PLUGGY_CHAVE_CIFRA"):
            decifrar("x")
