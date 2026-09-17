# FechaCaixa API

> Parte do [FechaCaixa](https://github.com/znt10/FechaCaixa). O frontend está em
> [FechaCaixa-front](https://github.com/znt10/FechaCaixa-front).

Backend do **FechaCaixa**: o fechamento de caixa das lojas de uma empresa —
lançamento, conferência e correção.

Django + Django REST Framework, MySQL, Celery para tarefas assíncronas.
A autenticação usa JWT guardado em cookies HTTP-only — o token nunca vai no
corpo da resposta, para não ficar acessível ao JavaScript.

## As duas metades

**O formulário** roda no celular da loja e **não tem login**. O aparelho digita
uma vez o código de acesso da empresa (algo como `PRIM-4821`) e recebe um cookie
HTTP-only que vale 180 dias. Daí em diante a empresa vem sempre desse cookie —
nunca de um parâmetro na URL, que era exatamente o que deixava ler as lojas de
qualquer empresa.

**O painel** é da gerência e tem login: quanto cada loja fez, o que falta
lançar, as saídas, os gráficos, e a tela da empresa.

## Como rodar

Precisa de Docker. Copie o exemplo de configuração e preencha:

```bash
cp .env.example .env
```

Suba os containers:

```bash
docker compose up --build        # em primeiro plano
docker compose up -d --build     # em segundo plano
docker compose down              # parar
```

Ao subir, o `entrypoint` coleta o estático, aplica as migrations, carrega os
grupos e cria ou atualiza o usuário admin a partir do `.env`.

| Onde | URL |
|---|---|
| API | http://localhost:8000 |
| Admin Django | http://localhost:8000/admin/ |
| Swagger | http://localhost:8000/api/schema/swagger/ |
| Redoc | http://localhost:8000/api/schema/redoc/ |

O Swagger é gerado do código, então não envelhece — é onde olhar a lista de
endpoints.

## Perfis de acesso

Todo usuário precisa estar em um grupo. Sem grupo, o login é recusado.

| Grupo | O que alcança |
|---|---|
| `Admin` | tudo, mais o Django Admin |
| `Gerente` | a empresa dele: código de acesso, lojas, quem retira dinheiro, funcionários — e o painel |
| `Funcionario` | o painel: vê, confere e corrige o caixa. Não administra a empresa |

O isolamento por empresa vive no `get_queryset` de cada ViewSet, não só na
tela: quem chamar a API direto continua vendo só a própria conta. As duas
pontas — quem entra na rota (`permissions.py`) e o que a pessoa vê
(`get_queryset`) — precisam ser lidas juntas.

### Quem decide: o grupo ou o código?

Os dois, em sequência.

```
grupo do usuário  →  código lê o grupo  →  decide
   (o crachá)          (o porteiro)        (403 ou 200)
```

O **grupo** é o dado. O **código** é a decisão: cada rota declara uma classe de
permissão, e a classe consulta o grupo pelo nome.

```python
# app/permissions.py — o porteiro
def is_gerente(user):
    return user.groups.filter(name=GRUPO_GERENTE).exists()

# app/api/v1/views/empresa.py — quem ele guarda
permission_classes = [IsAuthenticated, IsGerenteOrAdministrador]
```

**As permissões do Django não participam disso.** Nada no projeto chama
`has_perm`, e nenhuma view usa `DjangoModelPermissions`. Por isso a fixture
cria os grupos com `"permissions": []` — não é lista incompleta, é a lista
correta. As únicas permissões concedidas são as do grupo `Admin`, pelo
`ensure_admin`, e valem só dentro do `/admin/` do Django.

Os nomes ficam em `app/grupos.py`, num lugar só: `"Funcionario"` e
`"Funcionário"` são strings diferentes, e um acento a mais faria a checagem
devolver `False` sem erro nenhum.

**O que isso custa:** a proteção depende de cada view lembrar de declarar a
classe certa. Uma view nova que esqueça fica só com `IsAuthenticated`, e aí
qualquer funcionário entra — não há trava global que negue por padrão. É por
isso que `test_funcionarios.py` tem uma classe inteira (`OQueOFuncionarioNaoPodeTests`)
com um teste por rota que ele não pode alcançar: afrouxar uma delas quebra o
teste em vez de virar notícia.

O `Gerente` de uma empresa administra **as lojas dela**, e não só as que
estejam no nome dele. A regra antiga (`Loja.gerente`) vinha de quando o
projeto era um sistema de estoque com vários gerentes dividindo uma rede.

### Limites de taxa

Rotas abertas têm teto para não virarem porta de abuso:

| Escopo | Limite |
|---|---|
| `login` | 10/min |
| `codigo-da-empresa` | tentativas de código, por IP |
| `senha` | 10/hora |
| anônimo (geral) | 60/min |
| autenticado (geral) | 300/min |

O limite de `senha` é contado **pelo e-mail alvo**, não por quem pede: contar
por origem deixaria qualquer conta logada inundar a caixa de qualquer loja com
links de redefinição.

Os throttles do código de acesso são `SimpleRateThrottle`, e não
`AnonRateThrottle`, por um motivo específico: o `get_cache_key` do segundo
devolve `None` para requisição autenticada, então qualquer usuário logado
podia tentar códigos de empresa sem nenhum limite.

## Cache

Toda resposta sob `/api/` sai com `Cache-Control: no-store`
(`app/middleware.py`). Sem isso o DRF não manda cabeçalho nenhum, e uma
resposta sem informação de validade pode ser guardada pelo próprio navegador
pelo tempo que ele decidir — foi assim que a lista de lojas ficou congelando
no celular da loja mesmo depois de recarregar. O pedido não estava saindo do
aparelho.

## Testes

```bash
docker compose exec api python manage.py test app --noinput
```

Rodar um arquivo só:

```bash
docker compose exec api python manage.py test app.tests.test_permissoes --noinput
```

Os testes ficam em `backend/app/tests/`, um arquivo por assunto. Ao adicionar
um arquivo novo, confira o **número** de testes na saída (`Ran N tests`), não
só o `OK`: sem `__init__.py` na pasta o runner não acha nada e ainda assim
imprime `OK`.

Não rode duas suítes ao mesmo tempo: as duas disputam o mesmo banco de teste,
e o resultado sai truncado ou com falhas que não existem.

## Deploy

Coolify, pelo `docker-compose.prod.yml`. Ver
[docs/DEPLOY-COOLIFY.md](docs/DEPLOY-COOLIFY.md).

## Histórico

Este repositório nasceu como **Unistock**, um sistema de estoque e pedidos, e
virou o FechaCaixa. Estoque, produtos, pedidos, PDV, notificações in-app e o
bot de WhatsApp foram removidos na migration `0036_remove_unistock`. Alguns
comentários no código ainda citam o Unistock — onde citam, é para explicar por
que alguma coisa é do jeito que é.
