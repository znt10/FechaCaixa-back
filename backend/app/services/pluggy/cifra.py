"""Cifra do Client Secret da Pluggy (Fernet).

O segredo so existe em claro na memoria, na hora de pedir o token. No banco
fica o texto cifrado com a chave PLUGGY_CHAVE_CIFRA, que vem do ambiente.
"""
from cryptography.fernet import Fernet
from django.conf import settings


class CifraNaoConfigurada(Exception):
    """PLUGGY_CHAVE_CIFRA ausente ou invalida."""


MENSAGEM = (
    "PLUGGY_CHAVE_CIFRA nao configurada: gere uma chave com "
    "python -c \"from cryptography.fernet import Fernet; "
    "print(Fernet.generate_key().decode())\" e coloque no ambiente."
)


def _fernet() -> Fernet:
    chave = settings.PLUGGY_CHAVE_CIFRA
    if not chave:
        raise CifraNaoConfigurada(MENSAGEM)
    try:
        return Fernet(chave.encode())
    except (ValueError, TypeError) as exc:
        raise CifraNaoConfigurada(
            "PLUGGY_CHAVE_CIFRA invalida: precisa ser uma chave Fernet."
        ) from exc


def cifra_configurada() -> bool:
    try:
        _fernet()
    except CifraNaoConfigurada:
        return False
    return True


def cifrar(texto: str) -> str:
    return _fernet().encrypt(texto.encode()).decode()


def decifrar(cifrado: str) -> str:
    return _fernet().decrypt(cifrado.encode()).decode()
