"""Busca o extrato das conexoes da Pluggy: uma vez, ou para sempre a cada N segundos.

Roda num servico proprio do compose de producao (`--a-cada 21600`, 6 h), no
mesmo molde do ler_caixa_de_comprovantes: a producao nao tem worker nem beat.
"""

import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from app.services.pluggy.busca import buscar_todas
from app.services.pluggy.cifra import cifra_configurada

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Busca o extrato das conexoes da Pluggy."

    def add_arguments(self, parser):
        parser.add_argument(
            "--a-cada",
            type=int,
            default=0,
            help="Repete a cada N segundos. Sem isto, busca uma vez e sai.",
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
        if not cifra_configurada():
            logger.warning("PLUGGY_CHAVE_CIFRA vazia.")
            self.stdout.write("PLUGGY_CHAVE_CIFRA vazia.")
            return
        try:
            resultados = buscar_todas()
        except Exception:
            # Uma volta ruim (rede, Pluggy fora) nao pode matar o servico.
            logger.exception("Falha ao buscar o extrato da Pluggy.")
            return
        novas = sum(r.novas for r in resultados)
        adotadas = sum(r.adotadas for r in resultados)
        self.stdout.write(
            f"{len(resultados)} conexões, {novas} novas, {adotadas} adotadas."
        )
