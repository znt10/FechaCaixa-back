# Deploy no Railway

Dois servicos neste repo (API e worker do Celery) e um no repo do front, mais
os bancos. O Railway constroi pelo `Dockerfile` — o mesmo do desenvolvimento,
entao nao ha uma imagem "de producao" que so existe la e ninguem testa.

## Servicos

| Servico | Repo | Como sobe |
|---|---|---|
| `api` | FechaCaixa-back | Dockerfile + `railway.json` (healthcheck em `/healthz/`) |
| `worker` | FechaCaixa-back | mesmo Dockerfile, start command trocado |
| `front` | FechaCaixa-front | Dockerfile (alvo `prod`) |
| `MySQL` | plugin | painel do Railway |
| `Redis` | plugin | painel do Railway |

O `worker` usa o mesmo codigo e o mesmo banco, so com outro start command:

```
celery -A backend worker -l info
```

E o beat, se as tarefas agendadas forem ligadas:

```
celery -A backend beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

## Variaveis da API

O `${{...}}` e a sintaxe de referencia do proprio Railway: ele resolve na hora
do deploy, entao a senha do banco nao passa por aqui nem pelo git.

| Variavel | Valor |
|---|---|
| `SECRET_KEY` | gere uma: `python -c "import secrets;print(secrets.token_urlsafe(50))"` |
| `DEBUG` | `False` |
| `DATABASE_URL` | `${{MySQL.MYSQL_URL}}` |
| `CELERY_BROKER_URL` | `${{Redis.REDIS_URL}}` |
| `ALLOWED_HOSTS` | `${{RAILWAY_PRIVATE_DOMAIN}},healthcheck.railway.app` |
| `CSRF_TRUSTED_ORIGINS` | `https://<dominio-do-front>` |
| `CORS_ALLOWED_ORIGINS` | `https://<dominio-do-front>` |

### Sobre o ALLOWED_HOSTS

E o Django conferindo o cabecalho `Host` de cada requisicao. Sao dois valores,
e cada um resolve um caminho de entrada:

- `${{RAILWAY_PRIVATE_DOMAIN}}` — o front chama a API pela rede interna do
  Railway, entao o Host que chega e o dominio privado (algo como
  `api.railway.internal`). Sem ele, toda chamada do front vira 400.
- `healthcheck.railway.app` — o healthcheck do Railway manda esse Host. Sem
  ele o healthcheck responde 400, o Railway le como servico doente e mata o
  deploy. O sintoma e cruel: a aplicacao sobe, e e derrubada em seguida.

So acrescente `${{RAILWAY_PUBLIC_DOMAIN}}` se voce gerar um dominio publico
para a API — e normalmente nao precisa, porque quem fala com ela e o front,
por dentro.

`DATABASE_URL` e lido por `backend/banco.py`. As variaveis separadas
(`DB_ENGINE`, `DB_HOST`, ...) continuam funcionando e tem precedencia menor —
e o que o docker-compose local usa.

## Variaveis do front

| Variavel | Valor |
|---|---|
| `API_PROXY_URL` | a URL **interna** da API: `http://${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}` |
| `NODE_ENV` | `production` |

O front nao expoe a API ao navegador: toda chamada sai do proprio dominio dele
em `/backend/...` e o Next reescreve para o Django (ver `next.config.ts`). E
isso que faz os cookies HTTP-only serem first-party. Por isso a URL da API e
interna, e nao a publica — e por isso a API nao precisa de dominio publico.

## Se o login responder 403 "Usuario sem grupo"

Quer dizer que os grupos nao foram criados no boot. Antes isso era silencioso
— o entrypoint rodava `loaddata groups || true` — e o deploy ficava verde com
o sistema inutilizavel. Hoje o entrypoint para e mostra o erro; olhe o log do
deploy.

Para conferir num ambiente ja de pe:

```bash
railway run python manage.py shell -c "from django.contrib.auth.models import Group; print(list(Group.objects.values_list('name', flat=True)))"
```

Tem que sair `['Admin', 'Gerente', 'Funcionario', 'Responsavel']`. Se faltar
algum, `railway run python manage.py loaddata groups` resolve — e o proximo
deploy passa a fazer sozinho.

## Depois do primeiro deploy

1. O `entrypoint.sh` roda `collectstatic`, `migrate`, carrega os grupos e
   garante o admin. Nao ha passo manual de migracao.
2. Troque a senha do admin criado pelo `ensure_admin`.
3. Crie a empresa e o login da gerente pelo `/admin/`: um usuario no grupo
   `Gerente` com um `Perfil de usuario` apontando para a conta dela.
4. O codigo de acesso da empresa aparece para ela em `/empresa`.

## Por que o PWA so funciona depois disto

Service worker e instalacao exigem HTTPS. Em `localhost` o navegador abre
excecao (por isso da para testar na maquina de quem desenvolve), mas o celular
da loja chegando pelo IP da rede nao instala nada. O dominio do Railway ja vem
com HTTPS, entao e a partir daqui que a loja consegue por o formulario na tela
inicial.
