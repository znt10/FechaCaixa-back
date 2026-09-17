"""O catalogo de salgados inicial de uma conta.

Semeado sob demanda (ao listar) em vez de por migration de dados em todas as
contas, pelo mesmo motivo do plano de contas: a maioria das contas nao vende
salgado, e encher o banco delas com 42 linhas que ninguem pediu e ruido que
aparece no /admin de todo mundo.

Idempotente porque sai cedo quando a conta ja tem qualquer categoria: se a
empresa apagou "Salsicha" de proposito, semear de novo nao pode a
reintroduzir. A atomicidade garante que uma queda no meio nao deixa a conta
meio-semeada com o portao fechado para sempre.

A lista veio do seed do UniStock (`seed_produtos_unistock.py`), que sobreviveu
ao fim daquele projeto — e a lista real do negocio, nao um exemplo. Ficaram de
fora as categorias "Recheios" e "Mercado" do seed original: sao insumo e
limpeza, mundo de estoque, e nao produto final que a loja conta como
desperdicio.
"""

from django.db import IntegrityError, transaction

from app.models import CategoriaDeSalgado, Salgado

SALGADOS = [
    "Coxinha",
    "Risoles de queijo",
    "Risole presunto e queijo",
    "Bolinho de carne",
    "Kibe",
    "Kibe Queijo",
]

ESFIHAS = [
    "Carne",
    "Frango",
    "Bauru",
    "Calabresa",
    "Hamburger",
    "Salsicha com cheddar",
    "Torta de banana",
]

FOGAZZAS = [
    "Presunto e Queijo",
    "2 Queijos",
    "Calabresa",
    "Frango",
    "Pizza",
    "Chocolate",
    "Doce de leite",
]

# A ordem das chaves E a ordem da tela: grande antes de mini, em cada familia.
CATALOGO_INICIAL = {
    "Salgados grande": SALGADOS + ["Salsicha", "Bolinho ovo"],
    "Salgados mini": SALGADOS,
    "Esfihas grande": ESFIHAS,
    "Esfihas mini": ESFIHAS,
    "Fogazzas grande": FOGAZZAS,
    "Fogazzas mini": FOGAZZAS,
}


def garantir_catalogo_de_salgados(conta):
    """Semeia o catalogo se a conta ainda nao tem categoria nenhuma; se tem qualquer uma, sai cedo sem mexer.

    Roda dentro de um GET (a listagem semeia tambem, para quem abre a tela
    antes de cadastrar qualquer coisa), entao dois aparelhos abrindo o
    formulario ao mesmo tempo numa conta nova passam os dois pelo `if` acima
    antes de qualquer um ter semeado. @transaction.atomic garante que a
    escrita de cada um e tudo-ou-nada, mas nao os exclui um do outro — os
    dois tentam criar "Salgados grande" (etc) para a mesma conta, e o
    segundo esbarra na UniqueConstraint(conta, nome) do banco.
    Sem tratar isso, essa segunda corrida vira um IntegrityError cru, que o
    DRF devolve como 500 na PRIMEIRA abertura do formulario da loja — nao um
    caso raro, e sim o caso normal de duas pessoas no balcao ao mesmo tempo.

    A saida e deixar quem perdeu a corrida sair calado: o catalogo ja esta
    (ou esta sendo) semeado pelo outro request, entao nao ha nada a fazer
    aqui alem de nao explodir.
    """
    if CategoriaDeSalgado.objects.filter(conta=conta).exists():
        return

    try:
        with transaction.atomic():
            for ordem, (nome_da_categoria, itens) in enumerate(CATALOGO_INICIAL.items()):
                categoria = CategoriaDeSalgado.objects.create(
                    conta=conta, nome=nome_da_categoria, ordem=ordem
                )
                Salgado.objects.bulk_create(
                    [Salgado(categoria=categoria, nome=nome) for nome in itens]
                )
    except IntegrityError:
        # Outro request venceu a corrida e semeou primeiro (ou esta semeando
        # agora): a UniqueConstraint(conta, nome) da CategoriaDeSalgado
        # recusou a nossa criacao. O catalogo da conta continua correto —
        # nao ha reconciliacao a fazer, so nao propagar o 500.
        pass
