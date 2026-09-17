"""Importacao de uma nota fiscal: do XML ate a linha no banco.

Nao conhece HTTP de proposito. Quem chama pode ser a view do upload hoje, e a
leitura automatica da caixa de entrada amanha, sem que a regra mude de lugar.

A regra central do modulo mora aqui: nenhuma despesa entra sem prova, e a
mesma prova nao entra duas vezes.
"""

from django.db import transaction
from django.utils import timezone

from app.models import Fornecedor, Loja, NotaFiscal
from app.services.nfe import ler_nfe
from app.services.plano_de_contas import garantir_plano_de_contas


class RecusaDeNota(Exception):
    """O XML foi lido, mas a nota nao pode entrar. A mensagem vai para a tela."""

    def __init__(self, mensagem):
        self.mensagem = mensagem
        super().__init__(mensagem)


class NotaJaLancada(RecusaDeNota):
    pass


class DestinatarioNaoEhDaConta(RecusaDeNota):
    pass


class NotaDeSaida(RecusaDeNota):
    pass


def _formatar_cnpj(cnpj):
    """So para a mensagem de erro — a gerente le o CNPJ pontuado, nao cru."""
    if not cnpj or len(cnpj) != 14:
        return cnpj or "(sem CNPJ)"
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


@transaction.atomic
def importar_nota(xml_texto, conta, usuario=None):
    """A nota criada, ou uma RecusaDeNota / XmlNaoEhNFe explicando o motivo.

    Atomica porque a nota so faz sentido junto do fornecedor que ela cria: uma
    recusa depois do fornecedor gravado deixaria um nome na lista da tela sem
    nenhuma nota por tras.
    """
    dados = ler_nfe(xml_texto)

    # A recusa por saida vem antes da de destinatario: quando uma loja do grupo
    # vende para outra, os dois lados sao da conta, e o que aconteceu foi venda
    # — nao despesa.
    if Loja.objects.filter(conta=conta, cnpj=dados.cnpj_emitente).exists():
        raise NotaDeSaida(
            f"A nota {dados.numero} foi emitida pela sua propria loja "
            f"({_formatar_cnpj(dados.cnpj_emitente)}). Isso e venda, nao despesa."
        )

    # Sem escopo de conta de proposito: a chave e unica no Brasil inteiro, entao
    # colisao significa o mesmo documento fisico — e e assim que a duplicata e
    # pega mesmo se a nota foi lancada por outra empresa do sistema.
    ja_existe = NotaFiscal.objects.filter(chave=dados.chave).first()
    if ja_existe:
        if ja_existe.conta_id == conta.id:
            quem = (
                ja_existe.enviada_por.username if ja_existe.enviada_por else "alguem"
            )
            # created_at e auto_now_add, portanto UTC. Sem converter para o
            # fuso local, uma nota lancada de noite em Brasilia apareceria com
            # a data do dia seguinte na mensagem que a gerente le na tela.
            data_local = timezone.localtime(ja_existe.created_at)
            raise NotaJaLancada(
                f"A nota {dados.numero} ja foi lancada em "
                f"{data_local.strftime('%d/%m/%Y')} por {quem}."
            )
        # A nota existente e de outra conta: data e usuario sao informacao
        # daquela empresa, nao da que esta subindo agora, entao a mensagem
        # fica sem os dois — so confirma que a nota ja consta no sistema.
        raise NotaJaLancada(
            f"A nota {dados.numero} ja consta como lancada no sistema."
        )

    # O `if not` vem antes do filter de proposito: `filter(cnpj=None)` vira
    # `cnpj IS NULL` no SQL, e casaria com qualquer loja ainda sem CNPJ
    # cadastrado — uma nota emitida para pessoa fisica entraria como despesa de
    # uma loja escolhida a esmo.
    if not dados.cnpj_destinatario:
        raise DestinatarioNaoEhDaConta(
            f"A nota {dados.numero} foi emitida para uma pessoa fisica (CPF), "
            f"e por isso nao e despesa de loja nenhuma sua."
        )

    loja = Loja.objects.filter(conta=conta, cnpj=dados.cnpj_destinatario).first()
    if loja is None:
        raise DestinatarioNaoEhDaConta(
            f"A nota {dados.numero} foi emitida para "
            f"{_formatar_cnpj(dados.cnpj_destinatario)}, que nao e o CNPJ de "
            f"nenhuma loja sua. Confira o cadastro da loja."
        )

    garantir_plano_de_contas(conta)

    fornecedor, _ = Fornecedor.objects.get_or_create(
        conta=conta,
        cnpj=dados.cnpj_emitente,
        defaults={"razao_social": dados.nome_emitente},
    )

    return NotaFiscal.objects.create(
        conta=conta,
        loja=loja,
        fornecedor=fornecedor,
        chave=dados.chave,
        numero=dados.numero,
        serie=dados.serie,
        data_emissao=dados.data_emissao,
        valor_total=dados.valor_total,
        # Ja nasce classificada quando esse fornecedor tem historico: e a regra
        # de nao digitar duas vezes a mesma coisa.
        elemento=fornecedor.elemento_sugerido,
        xml_bruto=xml_texto,
        enviada_por=usuario,
    )
