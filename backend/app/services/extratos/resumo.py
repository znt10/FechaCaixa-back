"""Quanto entrou, quanto saiu, e para onde — por tipo e por categoria."""

from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

from app.models import TipoDeMovimento

ZERO = Decimal("0.00")
# Acima das 12 casas do campo: aqui e a soma do periodo de todas as lojas.
DINHEIRO = DecimalField(max_digits=16, decimal_places=2)


def _soma(filtro=None):
    return Coalesce(
        Sum("valor", filter=filtro, output_field=DINHEIRO),
        Value(ZERO),
        output_field=DINHEIRO,
    )


def _texto(valor):
    # String e nao float, pelo mesmo motivo dos totais das notas: float chega
    # ao front como 84320.099999.
    return str(Decimal(valor).quantize(ZERO))


def resumir(transacoes, consolidado):
    """O resumo do queryset ja filtrado.

    No consolidado (todas as lojas juntas), o que e transferencia entre lojas
    sai da conta: a Loja A mandou 500 para a B pagar o aluguel, e para a
    empresa o unico gasto foi o aluguel — contar a transferencia seria contar
    o mesmo dinheiro duas vezes. Olhando uma loja so, ela aparece normalmente,
    como saida de uma e entrada da outra.
    """
    if consolidado:
        transacoes = transacoes.exclude(categoria__entre_lojas=True)

    totais = transacoes.aggregate(
        entradas=_soma(Q(valor__gt=0)),
        saidas=_soma(Q(valor__lt=0)),
        pendentes_valor=_soma(Q(tipo__isnull=True)),
        pendentes_quantidade=Count("id", filter=Q(tipo__isnull=True)),
    )

    linhas = (
        transacoes.filter(tipo__isnull=False)
        .values(
            "tipo",
            "elemento__public_id",
            "elemento__nome",
            "elemento__grupo__nome",
            "categoria__public_id",
            "categoria__nome",
        )
        .annotate(total=_soma(), quantidade=Count("id"))
        .order_by("tipo", "total")
    )

    por_tipo = {
        tipo: {"tipo": tipo, "nome": rotulo, "valor": ZERO, "categorias": []}
        for tipo, rotulo in TipoDeMovimento.choices
    }
    for linha in linhas:
        bloco = por_tipo[linha["tipo"]]
        bloco["valor"] += linha["total"]
        if linha["elemento__public_id"]:
            nome = f'{linha["elemento__grupo__nome"]} > {linha["elemento__nome"]}'
            id_ = linha["elemento__public_id"]
        elif linha["categoria__public_id"]:
            nome = linha["categoria__nome"]
            id_ = linha["categoria__public_id"]
        else:
            # A categoria foi apagada pelo /admin e o SET_NULL deixou so o
            # tipo. O dinheiro continua no total do tipo, com nome proprio.
            nome, id_ = "Sem categoria", None
        bloco["categorias"].append({
            "id": str(id_) if id_ else None,
            "nome": nome,
            "valor": _texto(linha["total"]),
            "quantidade": linha["quantidade"],
        })

    for bloco in por_tipo.values():
        bloco["valor"] = _texto(bloco["valor"])

    return {
        "consolidado": consolidado,
        "entradas": _texto(totais["entradas"]),
        "saidas": _texto(totais["saidas"]),
        "pendentes": {
            "quantidade": totais["pendentes_quantidade"],
            "valor": _texto(totais["pendentes_valor"]),
        },
        "por_tipo": list(por_tipo.values()),
    }
