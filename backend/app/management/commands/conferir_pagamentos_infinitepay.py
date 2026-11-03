"""Confere os links da InfinitePay que receberam aviso e ainda nao foram
confirmados: uma vez, ou para sempre a cada N segundos.

E a rede de seguranca do aviso, para quando ele chega antes de o pagamento
cair ou o `payment_check` falha na hora. Roda num servico proprio do compose
de producao (`--a-cada 600`), no molde de `ler_caixa_de_comprovantes`.
"""

import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from app.services import infinitepay
from app.services.mensalidade import infinitepay_retorno

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Confere na InfinitePay os pagamentos da mensalidade que ficaram abertos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--a-cada",
            type=int,
            default=0,
            help="Repete a cada N segundos. Sem isto, confere uma vez e sai.",
        )

    def handle(self, *args, **opcoes):
        intervalo = opcoes["a_cada"]
        while True:
            # O MySQL derruba conexao parada; o laco vive dias.
            close_old_connections()
            self.uma_volta()
            if not intervalo:
                return
            time.sleep(intervalo)

    def uma_volta(self):
        if not infinitepay.configurada():
            self.stdout.write("A InfinitePay não está configurada.")
            return
        try:
            contagem = infinitepay_retorno.conferir_pendentes()
        except Exception:
            # Uma volta ruim (banco fora) nao pode matar o servico.
            logger.exception("Falha ao conferir os pagamentos da InfinitePay.")
            return
        self.stdout.write(f"Pagamentos conferidos: {contagem or 'nenhum'}.")
