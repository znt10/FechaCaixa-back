# Deploy no Coolify

Dois recursos do tipo **Docker Compose**: um para este repositório (API, worker,
beat, MySQL e Redis) e um para o repositório do front. O Coolify constrói pelo
`Dockerfile` — o mesmo do desenvolvimento, então não existe uma imagem "de
produção" que só existe lá e ninguém testa.

O que muda entre desenvolvimento e produção é o compose, não a imagem:

| Arquivo | Onde roda |
|---|---|
| `docker-compose.yml` | máquina de quem desenvolve (gunicorn `--reload`, bind mount, portas no host) |
| `docker-compose.prod.yml` | Coolify (sem porta publicada, quem expõe é o proxy) |

## 1. Recurso do backend

No projeto do Coolify: **+ New → Docker Compose** apontando para este repositório.

| Campo | Valor |
|---|---|
| Base Directory | `/` |
| Docker Compose Location | `/docker-compose.prod.yml` |
| Domínio | no serviço `api`, porta `8000` |

Os serviços que sobem: `api`, `worker`, `beat`, `db` (MySQL 8) e `redis`. Os
dois bancos ficam em volumes nomeados (`mysql_data`, `redis_data`) — o Coolify
os preserva entre deploys.

### Variáveis (aba Environment Variables)

| Variável | Valor |
|---|---|
| `SECRET_KEY` | gere uma: `python -c "import secrets;print(secrets.token_urlsafe(50))"` |
| `DB_PASSWORD` | senha do root do MySQL — inventada aqui, usada pelo `db` e pela `api` |
| `DB_NAME` | opcional, padrão `fechacaixa` |
| `ALLOWED_HOSTS` | `api.fechacaixa.io,api,127.0.0.1` |
| `CORS_ALLOWED_ORIGINS` | `https://fechacaixa.io` |
| `CSRF_TRUSTED_ORIGINS` | `https://fechacaixa.io` |
| `FRONTEND_URL` | `https://fechacaixa.io` |
| `DJANGO_SUPERUSER_EMAIL` | e-mail do admin inicial |
| `DJANGO_SUPERUSER_PASSWORD` | senha do admin inicial |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | conta SMTP (sem elas, nenhum e-mail sai) |
| `DEFAULT_FROM_EMAIL` | opcional |

O banco e o Redis não têm variável de endereço: `DB_HOST=db` e
`CELERY_BROKER_URL=redis://redis:6379/0` são fixos no compose, porque só fazem
sentido dentro desta rede.

### Sobre o ALLOWED_HOSTS

É o Django conferindo o cabeçalho `Host` de cada requisição, e cada valor da
lista cobre um caminho de entrada:

- **o domínio público da API** — como o proxy do Coolify entrega a requisição
  que veio da internet;
- **`api`** — o nome do serviço na rede interna, usado quando o front fala com
  o Django por dentro;
- **`127.0.0.1`** — o healthcheck do compose. Sem ele o Django responde 400, o
  container fica `unhealthy` e o deploy não termina, mesmo com a aplicação de
  pé. O sintoma é cruel: funciona, e mesmo assim não sobe.

## 2. Recurso do front

Outro recurso **Docker Compose**, apontando para o repositório do front.

| Campo | Valor |
|---|---|
| Base Directory | `/frontend` |
| Docker Compose Location | `/frontend/docker-compose.prod.yml` |
| Domínio | no serviço `front`, porta `3000` |

| Variável | Valor |
|---|---|
| `API_PROXY_URL` | `https://api.fechacaixa.io` (sem barra no fim) |

O navegador nunca fala com o Django: toda chamada sai do próprio domínio do
front em `/backend/...` e o servidor do Next reescreve (ver `next.config.ts`).
É isso que faz os cookies HTTP-only serem first-party. Por isso `API_PROXY_URL`
é lida no build **e** em execução — o destino do rewrite é gravado no build, e
o `proxy.ts` usa a mesma variável para renovar o token.

Front e backend são dois recursos, cada um com sua rede: o nome `api` **não**
resolve do lado do front por padrão. Ou você usa o domínio público da API
(o caminho simples), ou liga os dois na mesma rede pelo painel do Coolify
("Connect to Predefined Network") e aí sim aponta `API_PROXY_URL` para
`http://api:8000`.

## 3. Depois do primeiro deploy

1. O `entrypoint.sh` roda `collectstatic`, `migrate`, `garantir_grupos` e
   `ensure_admin`. Não há passo manual de migração.
2. Troque a senha do admin — pela variável `DJANGO_SUPERUSER_PASSWORD`, não
   pelo `/admin`: o `ensure_admin` é idempotente e redefine a senha a cada
   restart, então a alteração feita por lá volta no próximo deploy.
3. Crie a empresa e o login da gerente pelo `/admin/`: um usuário no grupo
   `Gerente` com um `Perfil de usuário` apontando para a conta dela.
4. O código de acesso da empresa aparece para ela em `/empresa`.

## Se o login responder 403 "Usuário sem grupo"

Quer dizer que os grupos não foram criados no boot. Isso já foi silencioso — o
entrypoint rodava `loaddata groups || true` — e o deploy ficava verde com o
sistema inutilizável. Hoje o entrypoint para e mostra o erro; olhe o log do
deploy.

Para conferir num ambiente já de pé, pelo terminal do serviço `api` no Coolify:

```bash
python manage.py shell -c "from django.contrib.auth.models import Group; print(list(Group.objects.values_list('name', flat=True)))"
```

Tem que sair `['Admin', 'Gerente', 'Funcionario', 'Responsavel']`.

## Por que o PWA só funciona depois disto

Service worker e instalação exigem HTTPS. Em `localhost` o navegador abre
exceção (por isso dá para testar na máquina de quem desenvolve), mas o celular
da loja chegando pelo IP da rede não instala nada. O Coolify emite o
certificado do domínio, então é a partir daqui que a loja consegue pôr o
formulário na tela inicial.
