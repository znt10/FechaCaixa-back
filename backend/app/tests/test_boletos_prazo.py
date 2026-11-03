from datetime import date

from django.test import TestCase

from app.models import Boleto, Conta
from app.services.boletos.prazo import resumo_de_prazos

HOJE = date(2026, 11, 2)


class ResumoDePrazosTests(TestCase):
    def test_faixas_disjuntas_so_dos_a_pagar(self):
        conta = Conta.objects.create(nome="Aurora")
        vencimentos = [
            None,  # sem data
            date(2026, 10, 31),  # vencido
            date(2026, 11, 2),  # hoje
            date(2026, 11, 3),  # 1 dia
            date(2026, 11, 5),  # 3 dias
            date(2026, 11, 6),  # 4 dias
            date(2026, 11, 9),  # 7 dias
            date(2026, 11, 10),  # 8 dias: fora do aviso
        ]
        for vencimento in vencimentos:
            Boleto.objects.create(conta=conta, origem="FORMULARIO", vencimento=vencimento)
        Boleto.objects.create(
            conta=conta, origem="FORMULARIO", vencimento=HOJE, situacao=Boleto.Situacao.PAGO
        )

        self.assertEqual(
            resumo_de_prazos(Boleto.objects.filter(conta=conta), HOJE),
            {"sem_data": 1, "vencidos": 1, "hoje": 1, "em_3_dias": 2, "em_7_dias": 2, "aviso": 7},
        )
