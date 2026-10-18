"""O que a Plataforma mostra da Pluggy: o painel, a linha de cada conexao e o
teste que lista as contas que a Pluggy enxerga.

Nada daqui devolve o Client Secret, nem cifrado.
"""

import re


from app.models import ConexaoPluggy, ContaBancaria

from .busca import ERRO_SEGREDO_ILEGIVEL
from .cifra import CifraNaoConfigurada, decifrar
from .cliente import ClientePluggy, FalhaDaPluggy

ERRO_SEM_CHAVE = "Defina PLUGGY_CHAVE_CIFRA no servidor antes de ligar a Pluggy."


class ErroDoTeste(Exception):
    """Falha ao testar a conexao; a mensagem ja e a frase para a tela."""

    def __init__(self, mensagem):
        super().__init__(mensagem)
        self.mensagem = mensagem


def _digitos(texto):
    return re.sub(r"\D", "", texto or "")


def _conta_da_empresa(conta_bancaria):
    return {
        "id": str(conta_bancaria.public_id),
        "numero": conta_bancaria.numero,
        "banco_nome": conta_bancaria.get_banco_display(),
        "loja": conta_bancaria.loja.nome_loja,
    }


def _contas_da_empresa(conta):
    return ContaBancaria.objects.filter(loja__conta=conta, ativo=True).select_related(
        "loja"
    )


def linha_da_conexao(conexao):
    contas = conexao.contas_bancarias.select_related("loja")
    return {
        "id": str(conexao.public_id),
        "empresa": {"id": str(conexao.conta.public_id), "nome": conexao.conta.nome},
        "email": conexao.email,
        "estado": conexao.estado,
        "client_id": conexao.client_id,
        "segredo_salvo_em": conexao.segredo_salvo_em,
        "item_ids": conexao.item_ids,
        "conectada_em": conexao.conectada_em,
        "ultima_busca_ok_em": conexao.ultima_busca_ok_em,
        "ultimo_erro": conexao.ultimo_erro,
        "contas": [_conta_da_empresa(c) for c in contas],
    }


def painel_da_pluggy():
    conexoes = list(ConexaoPluggy.objects.select_related("conta").order_by("created_at"))
    estados = [c.estado for c in conexoes]
    return {
        "contadores": {
            "funcionando": estados.count(ConexaoPluggy.Estado.ATIVA),
            "caidas": estados.count(ConexaoPluggy.Estado.COM_ERRO),
            "configurando": estados.count(ConexaoPluggy.Estado.CONFIGURANDO),
        },
        "conexoes": [linha_da_conexao(c) for c in conexoes],
    }


def _sugestao(numero, candidatas):
    digitos = _digitos(numero)
    if not digitos:
        return None
    for candidata in candidatas:
        if _digitos(candidata.numero) == digitos:
            return str(candidata.public_id)
    return None


def testar_conexao(conexao, cliente=None):
    """Pergunta a Pluggy quais contas cada item tem e sugere o par no FechaCaixa."""
    if cliente is None:
        try:
            cliente = ClientePluggy(
                conexao.client_id, decifrar(conexao.client_secret_cifrado)
            )
        except InvalidToken:
            raise ErroDoTeste(ERRO_SEGREDO_ILEGIVEL) from None
        except CifraNaoConfigurada:
            raise ErroDoTeste(ERRO_SEM_CHAVE) from None
    candidatas = list(_contas_da_empresa(conexao.conta))
    contas = []
    try:
        for item_id in conexao.lista_de_itens():
            for conta in cliente.contas(item_id):
                numero = conta.get("number") or ""
                contas.append(
                    {
                        "pluggy_account_id": conta["id"],
                        "numero": numero,
                        "nome": conta.get("name") or "",
                        "conta_bancaria_sugerida": _sugestao(numero, candidatas),
                    }
                )
    except FalhaDaPluggy as falha:
        raise ErroDoTeste(falha.mensagem) from None
    return {
        "contas": contas,
        "contas_da_empresa": [_conta_da_empresa(c) for c in candidatas],
    }
