"""As categorias iniciais de transferencia e recebimento de uma conta.

Semeadas sob demanda, como o plano de contas das notas
(app.services.plano_de_contas): a maioria das contas nunca liga o modulo.
"""

from django.db import transaction

from app.models import CategoriaDeMovimento, TipoDeMovimento

ENTRE_LOJAS = "Entre lojas"

CATEGORIAS_INICIAIS = {
    TipoDeMovimento.TRANSFERENCIA: [ENTRE_LOJAS, "Retirada de sócio", "Para terceiros"],
    TipoDeMovimento.RECEBIMENTO: ["Vendas (Pix/maquininha)", "Outros"],
}


@transaction.atomic
def garantir_categorias_de_movimento(conta):
    """Semeia as categorias se a conta ainda nao tem nenhuma.

    Sai cedo quando ja tem qualquer uma, pelo mesmo motivo do plano de contas:
    semear de novo reintroduziria o que a empresa desativou de proposito. A
    unica que sempre existe e a de entre lojas, porque o pareamento precisa
    dela — ver `categoria_entre_lojas`.
    """
    if not CategoriaDeMovimento.objects.filter(conta=conta).exists():
        for tipo, nomes in CATEGORIAS_INICIAIS.items():
            for nome in nomes:
                CategoriaDeMovimento.objects.create(
                    conta=conta, tipo=tipo, nome=nome, entre_lojas=(nome == ENTRE_LOJAS)
                )
    return categoria_entre_lojas(conta)


def categoria_entre_lojas(conta):
    """A categoria que o pareamento usa, criada se nao existir.

    Achada pela marca e nao pelo nome: a gerente pode renomear. Se o nome
    padrao ja estiver tomado por uma categoria comum da empresa, a nova ganha
    um sufixo em vez de estourar a restricao de nome unico.
    """
    existente = CategoriaDeMovimento.objects.filter(conta=conta, entre_lojas=True).first()
    if existente:
        return existente

    nome = ENTRE_LOJAS
    if CategoriaDeMovimento.objects.filter(
        conta=conta, tipo=TipoDeMovimento.TRANSFERENCIA, nome=nome
    ).exists():
        nome = f"{ENTRE_LOJAS} (automática)"
    return CategoriaDeMovimento.objects.create(
        conta=conta, tipo=TipoDeMovimento.TRANSFERENCIA, nome=nome, entre_lojas=True
    )
