"""Uma rodada de busca na Pluggy e as mudancas de estado que ela causa.

A conexao "cai" por tres motivos: falhas seguidas, banco com autorizacao
vencida (item fora de UPDATED) ou dado parado. O e-mail sai so na mudanca de
estado (conectou, caiu, voltou), nunca por rodada.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from cryptography.fernet import InvalidToken
from django.utils import timezone

from app.models import ConexaoPluggy, TransacaoBancaria

from ..extratos.api import gravar_linhas_da_api
from . import avisos
from .cifra import CifraNaoConfigurada, decifrar
from .cliente import ClientePluggy, FalhaDaPluggy
from .transacoes import linha_da_transacao

JANELA = timedelta(days=7)
PRIMEIRA_JANELA = timedelta(days=60)
PARADA_DEPOIS_DE = timedelta(hours=48)
FALHAS_PARA_CAIR = 3

# Meu Pluggy sincroniza todo dia: item atualizando nao e item caido. Qualquer
# outro status (LOGIN_ERROR, OUTDATED, WAITING_USER_INPUT, desconhecido) e erro.
STATUS_SAUDAVEIS = ("UPDATED", "UPDATING", "CREATED")

MOTIVO_AUTORIZACAO = (
    "A autorização no banco venceu ou foi cancelada. Reconecte a conta no Meu Pluggy."
)
ERRO_SEGREDO_ILEGIVEL = (
    "Não foi possível ler o Client Secret salvo. Cadastre de novo na Plataforma."
)
ERRO_SEM_CHAVE = "Defina PLUGGY_CHAVE_CIFRA no servidor antes de ligar a Pluggy."


@dataclass
class ResultadoDaBusca:
    conexao: ConexaoPluggy
    novas: int
    adotadas: int
    estado_antes: str
    estado_depois: str


def _instante(texto):
    try:
        return datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    except ValueError:
        return None


def motivo_da_queda(conexao, agora):
    """Por que a conexao esta caida, ou "" se esta de pe."""
    if conexao.falhas_seguidas >= FALHAS_PARA_CAIR:
        return conexao.ultimo_erro
    itens = conexao.itens.values()
    if any(item.get("status") not in STATUS_SAUDAVEIS for item in itens):
        return MOTIVO_AUTORIZACAO
    for item in itens:
        atualizado = _instante(item.get("atualizado_em"))
        if atualizado and atualizado < agora - PARADA_DEPOIS_DE:
            local = timezone.localtime(atualizado)
            return f"O Meu Pluggy não atualiza desde {local:%d/%m} às {local:%H:%M}."
    return ""


def _buscar_contas(conexao, cliente, agora):
    novas = adotadas = 0
    for conta_bancaria in conexao.contas_bancarias.filter(ativo=True).exclude(
        pluggy_account_id__isnull=True
    ).exclude(pluggy_account_id=""):
        ja_veio_da_api = TransacaoBancaria.objects.filter(
            conta_bancaria=conta_bancaria, id_externo__isnull=False
        ).exists()
        janela = JANELA if ja_veio_da_api else PRIMEIRA_JANELA
        transacoes = cliente.transacoes(
            conta_bancaria.pluggy_account_id,
            timezone.localtime(agora - janela).date(),
            timezone.localtime(agora).date(),
        )
        linhas = [l for l in map(linha_da_transacao, transacoes) if l is not None]
        resultado = gravar_linhas_da_api(conta_bancaria, linhas, json.dumps(transacoes))
        novas += resultado.novas
        adotadas += resultado.adotadas
    return novas, adotadas


def _rodar(conexao, cliente, agora):
    """Pede os itens e o extrato. Devolve (novas, adotadas); levanta FalhaDaPluggy."""
    for item_id in conexao.lista_de_itens():
        item = cliente.item(item_id)
        conexao.itens[item_id] = {
            "status": item.get("status"),
            "atualizado_em": item.get("lastUpdatedAt"),
        }
    return _buscar_contas(conexao, cliente, agora)


def buscar_conexao(conexao, agora=None, cliente=None):
    agora = agora or timezone.now()
    estado_antes = conexao.estado
    novas = adotadas = 0
    deu_certo = False
    try:
        if cliente is None:
            cliente = ClientePluggy(
                conexao.client_id, decifrar(conexao.client_secret_cifrado)
            )
        novas, adotadas = _rodar(conexao, cliente, agora)
        deu_certo = True
    except FalhaDaPluggy as falha:
        conexao.ultimo_erro = falha.mensagem
    except InvalidToken:
        conexao.ultimo_erro = ERRO_SEGREDO_ILEGIVEL
    except CifraNaoConfigurada:
        conexao.ultimo_erro = ERRO_SEM_CHAVE

    if deu_certo:
        conexao.falhas_seguidas = 0
        conexao.ultima_busca_ok_em = agora
    else:
        conexao.falhas_seguidas += 1

    primeira_boa = deu_certo and conexao.conectada_em is None
    if primeira_boa:
        conexao.conectada_em = agora

    motivo = motivo_da_queda(conexao, agora)
    caiu = conexao.estado == ConexaoPluggy.Estado.ATIVA and bool(motivo)
    voltou = conexao.estado == ConexaoPluggy.Estado.COM_ERRO and not motivo
    if caiu:
        conexao.estado = ConexaoPluggy.Estado.COM_ERRO
        conexao.ultimo_erro = motivo
    elif voltou:
        conexao.estado = ConexaoPluggy.Estado.ATIVA
    if deu_certo and not motivo:
        # Falha solta que ja passou: nao deixa o texto velho na tela.
        conexao.ultimo_erro = ""
    conexao.save()

    if primeira_boa:
        avisos.avisar_conectada(conexao, novas + adotadas)
    if caiu:
        avisos.avisar_caiu(conexao)
    if voltou:
        avisos.avisar_voltou(conexao)

    return ResultadoDaBusca(conexao, novas, adotadas, estado_antes, conexao.estado)


def buscar_todas(agora=None):
    conexoes = ConexaoPluggy.objects.filter(
        estado__in=[ConexaoPluggy.Estado.ATIVA, ConexaoPluggy.Estado.COM_ERRO]
    ).select_related("conta")
    return [buscar_conexao(conexao, agora=agora) for conexao in conexoes]
