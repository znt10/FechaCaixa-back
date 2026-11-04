"""Le a caixa de comprovantes: uma vez, ou para sempre a cada N segundos.

Roda num servico proprio do compose de producao (`--a-cada 60`). A producao
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

    def caixas(self):
        """(endereco, senha, pessoal) de cada caixa: a das lojas e a do gasto pessoal."""
        return [
            (settings.COMPROVANTES_EMAIL, settings.COMPROVANTES_SENHA_APP, False),
            (settings.COMPROVANTES_PESSOAL_EMAIL, settings.COMPROVANTES_PESSOAL_SENHA_APP, True),
        ]

    def uma_volta(self):
        for endereco, senha, pessoal in self.caixas():
            nome = "pessoal" if pessoal else "lojas"
            if not endereco or not senha:
                logger.warning("Caixa de comprovantes (%s) sem endereco ou senha.", nome)
                self.stdout.write(f"{nome}: caixa de comprovantes não configurada.")
                continue
            # Cada caixa no seu try: senha errada na pessoal nao pode parar a
            # entrada dos comprovantes das lojas, nem o contrario.
            try:
                caixa = CaixaImap(endereco, senha)
                try:
                    resumo = ler_caixa(caixa, pessoal=pessoal)
                finally:
                    caixa.fechar()
            except Exception:
                # Uma volta ruim (rede, Gmail fora) nao pode matar o servico.
                logger.exception("Falha ao ler a caixa de comprovantes (%s).", nome)
                continue
            self.stdout.write(
                f"{nome}: {resumo.emails} e-mails, {resumo.comprovantes} comprovantes, "
                f"{resumo.ignorados} ignorados, {resumo.falhas} com falha."
            )
