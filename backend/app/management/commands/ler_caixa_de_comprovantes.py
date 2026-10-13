"""Le a caixa de comprovantes: uma vez, ou para sempre a cada N segundos.

Roda num servico proprio do compose de producao (`--a-cada 300`). A producao
nao tem worker nem beat do Celery, e trazer os dois de volta so para isto
custaria um broker, dois servicos e uma tabela de agendamento.
"""

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from app.services.comprovantes.caixa import CaixaImap, ler_caixa

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Le a caixa de e-mail dos comprovantes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--a-cada",
            type=int,
            default=0,
            help="Repete a cada N segundos. Sem isto, le uma vez e sai.",
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
        endereco = settings.COMPROVANTES_EMAIL
        senha = settings.COMPROVANTES_SENHA_APP
        if not endereco or not senha:
            logger.warning("COMPROVANTES_EMAIL ou COMPROVANTES_SENHA_APP vazios.")
            self.stdout.write("Caixa de comprovantes não configurada.")
            return
        try:
            caixa = CaixaImap(endereco, senha)
            try:
                resumo = ler_caixa(caixa)
            finally:
                caixa.fechar()
        except Exception:
            # Uma volta ruim (rede, Gmail fora) nao pode matar o servico.
            logger.exception("Falha ao ler a caixa de comprovantes.")
            return
        self.stdout.write(
            f"{resumo.emails} e-mails, {resumo.comprovantes} comprovantes, "
            f"{resumo.ignorados} ignorados."
        )
