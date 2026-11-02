"""Quantos boletos pedem atencao, por faixa de prazo.

E o que vira a marquinha da aba e a faixa do painel. As faixas nao se
sobrepoem: "aviso" e a soma das cinco e conta cada boleto uma vez so.
"""

from datetime import timedelta

from django.db.models import Count, Q

from app.models import Boleto

# Ate quantos dias para vencer o boleto entra no aviso, e ate quantos o tom e
# mais forte.
DIAS_DO_AVISO = 7
DIAS_DO_DESTAQUE = 3


def resumo_de_prazos(boletos, hoje):
    a_pagar = boletos.filter(situacao=Boleto.Situacao.A_PAGAR)
    destaque = hoje + timedelta(days=DIAS_DO_DESTAQUE)
    aviso = hoje + timedelta(days=DIAS_DO_AVISO)

    resumo = a_pagar.aggregate(
        sem_data=Count("id", filter=Q(vencimento__isnull=True)),
        vencidos=Count("id", filter=Q(vencimento__lt=hoje)),
        hoje=Count("id", filter=Q(vencimento=hoje)),
        em_3_dias=Count("id", filter=Q(vencimento__gt=hoje, vencimento__lte=destaque)),
        em_7_dias=Count("id", filter=Q(vencimento__gt=destaque, vencimento__lte=aviso)),
    )
    resumo["aviso"] = sum(resumo.values())
    return resumo
