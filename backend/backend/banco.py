"""Le a configuracao do banco a partir de uma URL unica.

Todo provedor de deploy (Railway, Render, Fly, Heroku) entrega o banco assim,
numa variavel so. Sem isto, a URL teria que ser picada a mao em seis variaveis
no painel — e um erro de digitacao ali aparece la na frente como "nao conecta",
longe da causa.

Sem dependencia nova (dj-database-url): a stdlib ja parte a URL, e o que falta
e o mapa de esquema para engine.
"""

from urllib.parse import unquote, urlparse

ENGINES = {
    "mysql": "django.db.backends.mysql",
    "postgres": "django.db.backends.postgresql",
    "postgresql": "django.db.backends.postgresql",
    "sqlite": "django.db.backends.sqlite3",
}


def banco_da_url(url):
    """Devolve o dict de DATABASES['default'], ou None se nao houver URL."""
    if not url:
        return None

    partes = urlparse(url)
    engine = ENGINES.get(partes.scheme)
    if engine is None:
        # Estourar no boot e melhor do que subir apontando para lugar nenhum:
        # o erro aparece no log do deploy, com o esquema errado no texto.
        raise ValueError(
            f"DATABASE_URL com esquema desconhecido: {partes.scheme!r}. "
            f"Conhecidos: {', '.join(sorted(ENGINES))}."
        )

    return {
        "ENGINE": engine,
        "NAME": partes.path.lstrip("/"),
        # Usuario e senha vem percent-encoded quando o provedor gera a senha:
        # sem decodificar, um "@" chega como "%40" e o banco recusa.
        "USER": unquote(partes.username or ""),
        "PASSWORD": unquote(partes.password or ""),
        "HOST": partes.hostname or "",
        "PORT": str(partes.port or ""),
    }
