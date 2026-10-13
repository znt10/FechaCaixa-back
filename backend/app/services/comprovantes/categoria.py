"""A categoria aprendida pelo nome de quem recebeu."""

from app.models import RegraDeCategoriaDoRecebedor


def categoria_sugerida(conta, recebedor_normalizado):
    if not recebedor_normalizado:
        return None
    regra = (
        RegraDeCategoriaDoRecebedor.objects.filter(
            conta=conta, recebedor_normalizado=recebedor_normalizado
        )
        .select_related("elemento")
        .first()
    )
    return regra.elemento if regra else None


def aprender_categoria(comprovante):
    """Grava (ou troca) a regra do recebedor. Limpar a categoria nao apaga a regra."""
    if comprovante.elemento_id is None or not comprovante.recebedor_normalizado:
        return
    RegraDeCategoriaDoRecebedor.objects.update_or_create(
        conta=comprovante.conta,
        recebedor_normalizado=comprovante.recebedor_normalizado,
        defaults={"elemento": comprovante.elemento},
    )
