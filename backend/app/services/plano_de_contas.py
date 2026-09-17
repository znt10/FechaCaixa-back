"""O plano de contas inicial de uma conta.

Semeado sob demanda (no upload e ao listar os elementos) em vez de por
migration de dados em todas as contas: a maioria das contas nunca vai ligar o
modulo, e encher o banco delas com linhas que ninguem pediu e ruido que
aparece no /admin de todo mundo.

Idempotente porque sai cedo quando a conta ja tem qualquer grupo: se a empresa
apagou "Combustivel" de proposito, semear de novo nao pode o reintroduzir. A
atomicidade garante que uma queda no meio nao deixa a conta meio-semeada com
o portao fechado para sempre.
"""

from django.db import transaction

from app.models import ElementoDeDespesa, GrupoDeDespesa

PLANO_INICIAL = {
    "Compras": ["Embalagem", "Materia-prima", "Bebidas"],
    "Operacional": ["Combustivel", "Aluguel", "Energia", "Manutencao"],
    "Pessoal": ["Salario", "Vale-transporte", "Uniforme"],
    "Impostos": ["Simples Nacional", "Taxas"],
}


@transaction.atomic
def garantir_plano_de_contas(conta):
    """Semeia o plano completo se a conta ainda nao tem grupo nenhum; se tem qualquer um, sai cedo sem mexer."""
    if GrupoDeDespesa.objects.filter(conta=conta).exists():
        # A conta ja tem plano proprio: semear de novo reintroduziria o que ela
        # apagou de proposito.
        return

    for nome_do_grupo, elementos in PLANO_INICIAL.items():
        grupo = GrupoDeDespesa.objects.create(conta=conta, nome=nome_do_grupo)
        for nome_do_elemento in elementos:
            ElementoDeDespesa.objects.create(grupo=grupo, nome=nome_do_elemento)
