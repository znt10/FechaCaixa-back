"""Cadastra em lote os funcionarios de uma empresa, a partir de uma lista.

Existe por uma conta simples: a empresa tem 60 funcionarios, e digitar 60 nomes
um a um na tela Empresa e o tipo de trabalho em que se erra a grafia de metade
— e nome com grafia errada e o que faz o consumo do mes nao fechar.

Idempotente comparando sem acento e sem caixa: rodar duas vezes com a mesma
lista nao duplica ninguem, e "MARIA SOUZA" nao vira uma segunda pessoa ao lado
de "Maria Souza". A grafia gravada e a do arquivo, que e a que a empresa usa.

Por padrao ninguem cadastrado aqui fecha caixa: sao 60 pessoas e ~20 que
lancam, entao a marca certa e a excecao, feita depois na tela Empresa (ou com
--pode-lancar-caixa, para importar a lista dos gerentes).

    manage.py importar_encarregados primavera funcionarios.txt
    manage.py importar_encarregados primavera gerentes.txt --pode-lancar-caixa
"""

import unicodedata

from django.core.management.base import BaseCommand, CommandError

from app.models import Conta, Encarregado


def chave(nome):
    """O nome sem acento, sem caixa e sem espaco sobrando.

    Mesma normalizacao da migracao 0040, que criou os encarregados a partir do
    historico: duas versoes divergentes fariam a importacao criar de novo
    alguem que ja existe.
    """
    sem_acento = "".join(
        letra
        for letra in unicodedata.normalize("NFD", nome)
        if unicodedata.category(letra) != "Mn"
    )
    return " ".join(sem_acento.lower().split())


class Command(BaseCommand):
    help = "Cadastra funcionarios de uma empresa a partir de um arquivo de nomes."

    def add_arguments(self, parser):
        parser.add_argument("conta", help="slug ou nome da empresa")
        parser.add_argument("arquivo", help="um nome por linha")
        parser.add_argument(
            "--pode-lancar-caixa",
            action="store_true",
            help="marca os importados como quem fecha o caixa",
        )

    def handle(self, *args, **opcoes):
        conta = (
            Conta.objects.filter(slug=opcoes["conta"]).first()
            or Conta.objects.filter(nome__iexact=opcoes["conta"]).first()
        )
        if not conta:
            raise CommandError(f"Empresa '{opcoes['conta']}' nao encontrada.")

        try:
            with open(opcoes["arquivo"], encoding="utf-8") as arquivo:
                nomes = [linha.strip() for linha in arquivo if linha.strip()]
        except OSError as erro:
            raise CommandError(f"Nao consegui ler o arquivo: {erro}") from erro

        existentes = {
            chave(e.nome): e for e in Encarregado.objects.filter(conta=conta)
        }

        criados, repetidos = 0, 0
        for nome in nomes:
            if chave(nome) in existentes:
                repetidos += 1
                continue
            pessoa = Encarregado.objects.create(
                conta=conta,
                nome=nome,
                pode_lancar_caixa=opcoes["pode_lancar_caixa"],
            )
            existentes[chave(nome)] = pessoa
            criados += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"{conta.nome}: {criados} cadastrados, {repetidos} ja existiam "
                f"({len(nomes)} nomes no arquivo)."
            )
        )
        if not opcoes["pode_lancar_caixa"] and criados:
            self.stdout.write(
                "Nenhum deles fecha caixa. Marque os que fecham na tela Empresa."
            )
