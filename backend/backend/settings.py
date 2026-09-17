import os
import sys
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .banco import banco_da_url


BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "SECRET_KEY precisa estar definida no ambiente (veja .env)."
    )

DEBUG = os.getenv("DEBUG", "False").lower() in ("1", "true", "yes", "on")


def lista_do_ambiente(nome):
    """Lista separada por virgula vinda do .env.

    Sem valor padrao de proposito: host e URL de producao nao ficam no codigo
    versionado. Falta a variavel? Erro explicito, e nao um AttributeError de
    NoneType.split() que nao diz o que fazer.
    """
    valor = os.getenv(nome)
    if not valor:
        raise ImproperlyConfigured(
            f"{nome} precisa estar definida no ambiente (veja .env.example)."
        )
    return [item.strip() for item in valor.split(",") if item.strip()]


ALLOWED_HOSTS = lista_do_ambiente("ALLOWED_HOSTS")


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_spectacular",
    "django_celery_beat",
    "app",
]

MIDDLEWARE = [
    # Primeiro da lista: na resposta o Django percorre a lista ao contrario,
    # entao daqui ele carimba por ultimo e ganha de qualquer Cache-Control
    # que tenha sido posto mais abaixo.
    "app.middleware.SemCacheNaApi",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Logo depois do SecurityMiddleware, como o WhiteNoise pede.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

ROOT_URLCONF = "backend.urls"

WSGI_APPLICATION = "backend.wsgi.application"
ASGI_APPLICATION = "backend.asgi.application"

# Ordem de preferencia: DATABASE_URL (como todo provedor de deploy entrega),
# depois as variaveis separadas (como o docker-compose local usa), e por fim
# sqlite (para quem so quer rodar os testes).
_banco_da_url = banco_da_url(os.getenv("DATABASE_URL"))

if _banco_da_url:
    DATABASES = {"default": _banco_da_url}
elif os.getenv("DB_ENGINE"):
    DATABASES = {
        "default": {
            "ENGINE": os.getenv("DB_ENGINE"),
            "NAME": os.getenv("DB_NAME"),
            "USER": os.getenv("DB_USER"),
            "PASSWORD": os.getenv("DB_PASSWORD"),
            "HOST": os.getenv("DB_HOST"),
            "PORT": os.getenv("DB_PORT"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
# Com DEBUG=False o Django para de servir estatico, e o /admin sobe sem CSS —
# parece quebrado, e nao esta. O WhiteNoise serve os arquivos do proprio
# processo, que e o que faz sentido num deploy sem servidor de arquivos na
# frente: o proxy do Coolify so repassa o que o gunicorn responder.
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# CORS / CSRF
CORS_ALLOW_CREDENTIALS = True

CORS_ALLOWED_ORIGINS = lista_do_ambiente("CORS_ALLOWED_ORIGINS")

CSRF_TRUSTED_ORIGINS = lista_do_ambiente("CSRF_TRUSTED_ORIGINS")

SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# O default do Django (100) e menor que o teto de 500 arquivos por lote do
# upload de notas fiscais (app.api.v1.views.notas.LIMITE_DE_ARQUIVOS_POR_LOTE):
# sem levantar este numero, uma requisicao com mais de 100 arquivos nunca
# chegaria na view — o Django a rejeitaria antes, com um 400 sem a mensagem
# explicando o motivo. 600 da folga acima dos 500 para a view ser quem
# responde primeiro, com a mensagem certa.
DATA_UPLOAD_MAX_NUMBER_FILES = 600

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "app.authentication.CookieJWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "300/min",
        "login": "10/min",
        "registro": "20/hour",
        "senha": "10/hour",
        "acesso-formulario-min": "5/min",
        "acesso-formulario-hora": "20/hour",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "FechaCaixa API",
    "DESCRIPTION": "API do FechaCaixa: fechamento de caixa das lojas.",
    "VERSION": "1.0.0",
}

# ─── Celery / Redis ───────────────────────────────────────────────────────────
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
# Nos testes as tasks rodam sincronas, sem broker.
CELERY_TASK_ALWAYS_EAGER = "test" in sys.argv
CELERY_TASK_EAGER_PROPAGATES = True

# ─── Email ────────────────────────────────────────────────────────────────────
# Em DEBUG os emails vao para o console; em producao, SMTP via env. EMAIL_BACKEND
# no .env sobrepoe isso — usado pra testar envio real (Gmail) com DEBUG=True local.
if os.getenv("EMAIL_BACKEND"):
    EMAIL_BACKEND = os.getenv("EMAIL_BACKEND")
elif DEBUG:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() in ("1", "true", "yes", "on")
DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL", "FechaCaixa <no-reply@fechacaixa.local>"
)
# Timeout curto: dentro do container a conexao SMTP tenta o IPv6 do Gmail
# primeiro, que
# cai num buraco negro e so estoura no timeout padrao do TCP (~134s) antes de
# cair pro IPv4. Com 10s a tentativa IPv6 desiste rapido e o fallback IPv4 envia.
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "10"))
# URL do front, usada para montar links em emails (confirmacao de conta).
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
