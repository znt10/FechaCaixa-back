# Deploy no Coolify

Dois recursos do tipo **Docker Compose**: um para este repositório (API e
MySQL) e um para o repositório do front. O Coolify constrói pelo
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

Os serviços que sobem são dois: `api` e `db` (MySQL 8), com o banco num volume
nomeado (`mysql_data`) que o Coolify preserva entre deploys.

Não há `worker`, `beat` nem Redis. O beat existia para uma tarefa agendada
(`enviar_digest_lojas`) que saiu junto com o estoque na migration `0036`, e o
worker não tinha mais nada para entregar além dela e de um e-mail. Esse e-mail
— o de "esqueci a senha" — passa a sair dentro da própria requisição, por
`CELERY_TASK_ALWAYS_EAGER`. Se um dia voltar a existir tarefa pesada o
suficiente para não caber numa requisição, os três serviços voltam com ela.

### Variáveis (aba Environment Variables)

| Variável | Valor |
|---|---|
| `SECRET_KEY` | gere uma: `python -c "import secrets;print(secrets.token_urlsafe(50))"` |
| `DB_PASSWORD` | senha do root do MySQL — inventada aqui, usada pelo `db` e pela `api` |
| `DB_NAME` | opcional, padrão `fechacaixa` |
| `ALLOWED_HOSTS` | `api.fechacaixa.io,api` |
| `CORS_ALLOWED_ORIGINS` | `https://fechacaixa.io` |
| `CSRF_TRUSTED_ORIGINS` | `https://fechacaixa.io` |
| `FRONTEND_URL` | `https://fechacaixa.io` |
| `DJANGO_SUPERUSER_EMAIL` | e-mail do admin inicial |
| `DJANGO_SUPERUSER_PASSWORD` | senha do admin inicial |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | conta SMTP (sem elas, nenhum e-mail sai — inclusive o de "esqueci a senha") |
| `DEFAULT_FROM_EMAIL` | opcional |

O banco não tem variável de endereço: `DB_HOST=db` é fixo no compose, porque só
faz sentido dentro desta rede.

Se o painel ainda pedir `EVOLUTION_API_KEY` ou `EVOLUTION_POSTGRES_PASSWORD`,
são sobras da versão anterior do compose guardadas pelo Coolify — o repositório
não lê nenhuma das duas. Apague-as na própria aba.

### Sobre o ALLOWED_HOSTS

É o Django conferindo o cabeçalho `Host` de cada requisição, e cada valor da
lista cobre um caminho de entrada:

- **o domínio público da API** — como o proxy do Coolify entrega a requisição
  que veio da internet;
- **`api`** — o nome do serviço na rede interna, usado quando o front fala com
  o Django por dentro.

O healthcheck não precisa entrar nessa lista: o `docker/healthcheck.py` lê o
próprio `ALLOWED_HOSTS` e manda o primeiro nome como `Host`, justamente para as
duas pontas não poderem discordar. Antes ele batia com `Host: 127.0.0.1`, e
quem não adivinhasse que precisava pôr esse valor aqui via o container ficar
`Degraded` com a aplicação inteira funcionando.

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

## Se o deploy falhar com "dependency db failed to start"

Quer dizer que o MySQL nao passou no healthcheck dentro da janela. Na primeira
subida ele cria o banco do zero, e num servidor ocupado — logo depois de um
build, por exemplo — isso demora. O `start_period` de 180s cobre esse caso: ali
dentro, tentativa que falha nao conta.

Se estourar mesmo assim, o motivo esta no log do proprio container do banco, e
nao no log do deploy. No Coolify, aba **Logs** do recurso, servico `db`. Pelo
terminal do servidor:

```bash
docker logs $(docker ps -a --format '{{.Names}}' | grep '^db-' | head -1) | tail -40
```

Duas causas que aparecem por ali: disco cheio, e uma senha que chegou
diferente do que esta no painel. Esta segunda vale olhar quando o log do deploy
traz linhas como:

```
level=warning msg="The \"b\" variable is not set. Defaulting to a blank string."
```

E o Compose lendo um `$` dentro de um **valor** como se fosse variavel: uma
senha `abc$bdef` chega no container como `abcdef`. Ou troque o valor por um sem
`$`, ou escreva `$$` no lugar de cada `$`.

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
