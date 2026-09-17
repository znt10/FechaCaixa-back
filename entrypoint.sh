#!/bin/sh
set -e

# Nada aqui tem "|| true", e isso e a licao de um deploy quebrado.
#
# Onde hoje esta o garantir_grupos havia "loaddata groups || true". A fixture
# citava permissoes de "produto" e "estoque", modelos apagados na migration
# 0036 — num banco novo o content type nao existe, o loaddata estourava
# DeserializationError, e o "|| true" engolia. O deploy ficava verde, os
# grupos nao eram criados, e TODO login respondia 403 "Usuario sem grupo".
# Um sistema em que ninguem entra e pior do que um deploy que para e diz por
# que.

python manage.py collectstatic --noinput
python manage.py migrate
python manage.py garantir_grupos
python manage.py ensure_admin

exec "$@"
