"""Healthcheck do container da api: /healthz/ visto de dentro.

A chamada vai para 127.0.0.1, mas o cabecalho Host e o primeiro nome do
ALLOWED_HOSTS. Isso nao e detalhe: com DEBUG=False o Django confere o Host de
toda requisicao e responde 400 para o que nao esta na lista. Batendo em
127.0.0.1 com o Host padrao, o healthcheck so passaria se alguem tivesse
lembrado de por "127.0.0.1" no ALLOWED_HOSTS do painel — e quando nao punha, o
container ficava "unhealthy" com a aplicacao inteira funcionando. Lendo a
mesma variavel que o Django le, as duas pontas nao tem como discordar.

Sai 0 se a api responde 200 (o que inclui o banco respondendo), 1 em qualquer
outro caso.
"""

import os
import sys
import urllib.request

hosts = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "").split(",") if h.strip()]
# Sem ALLOWED_HOSTS o Django nem sobe, entao a lista vazia aqui e teorica.
host = hosts[0] if hosts else "127.0.0.1"

requisicao = urllib.request.Request(
    "http://127.0.0.1:8000/healthz/", headers={"Host": host}
)

try:
    with urllib.request.urlopen(requisicao, timeout=5) as resposta:
        sys.exit(0 if resposta.status == 200 else 1)
except Exception as erro:
    # A mensagem vai para o log do healthcheck (docker inspect), que e onde
    # alguem vai olhar quando o painel disser so "unhealthy".
    print(f"healthcheck falhou: {erro}", file=sys.stderr)
    sys.exit(1)
