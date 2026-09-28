# Limite por IP na API

Os throttles do DRF (`anon` 60/min, `login` 10/min, o acesso ao formulário
5/min e 20/hora, o cadastro) contam pedidos por IP. Para essa conta valer,
três coisas precisam estar certas. Antes desta mudança nenhuma estava.

## O que cada peça resolve

1. **O contador fica no Redis** (`CACHES` no `settings.py`, `CACHE_REDIS_URL`
   no compose de produção). Sem isso ele era o `LocMemCache`, um por processo:
   com `--workers 3`, cada IP tinha três contadores e o limite valia o triplo.
   O serviço `redis` do `docker-compose.prod.yml` só guarda esses contadores,
   então não tem volume. Sem `CACHE_REDIS_URL` (testes, dev) o cache volta a
   ser o LocMem.
2. **Vale o último IP do `X-Forwarded-For`** (`NUM_PROXIES: 1`). Com o padrão
   (`None`), o DRF usava o cabeçalho **inteiro** como identidade. Quem
   mandasse um valor novo a cada pedido ganhava um contador novo a cada
   pedido, e nunca batia no limite. O último da lista é o que o Traefik do
   Coolify acrescentou.
3. **O IP do visitante atravessa o Next.** O navegador chama `/backend/*` no
   domínio do front, e o Next reescreve para a URL **pública** da API:
   `Traefik → Next → internet → Traefik → Django`. Para o Django, o último IP
   é o do servidor do front, igual para todo visitante, e o site inteiro
   dividiria um contador só. Um pico de acesso viraria 429 para todo mundo.

   O `proxy.ts` do front manda o IP de verdade em `X-Cliente-IP`, com o
   `PROXY_SEGREDO` em `X-Proxy-Segredo`. O `IpDoProxyMiddleware`
   (`app/middleware.py`) só troca o `X-Forwarded-For` por esse IP quando o
   segredo confere. Sem segredo, o cabeçalho é ignorado e vale o item 2.

## Configurar

`PROXY_SEGREDO` precisa ser **igual** nos dois recursos do Coolify, o do
backend e o do front. Gere com `openssl rand -hex 32`. O compose do backend
recusa subir sem ele. O front sobe sem, mas aí todo visitante volta a dividir
o IP do servidor.

## Testes

`app/tests/test_limite_por_ip.py` no back e `proxy-ip-do-cliente.test.ts` no
front. Os testes que usam rota com throttle limpam o cache no `setUp`: sem
isso, as tentativas de um teste contam no seguinte.

## Conferir em produção

Depois do deploy:

1. De fora, 11 POSTs seguidos em `https://fechacaixa.io/backend/login/`: o
   11º tem de responder 429.
2. Logo em seguida, de **outra rede** (o 4G do celular), o login ainda tem de
   responder normalmente. Se também der 429, o IP não está atravessando o
   Next: confira se o `PROXY_SEGREDO` é o mesmo nos dois recursos.
