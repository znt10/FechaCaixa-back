"""Guardar um boleto: do arquivo ate a linha no banco.

Nao conhece HTTP, como o registro da nota: quem chama e o formulario da loja
ou o painel, e a regra mora aqui.

A ordem e a regra central. Grava ANTES de ler: o celular da loja pode tirar
uma foto que ninguem le, e a foto guardada com o vencimento digitado ja e o
que a gerencia precisa. Depois le, e a leitura so completa o que ficou
vazio — o que a loja digitou olhando o papel vale mais.

E le de graca primeiro: o texto do PDF e o codigo de barras da foto dao a
linha digitavel inteira. A IA custa a cada leitura e so entra quando isso
nao basta para saber o vencimento.

O mesmo boleto nao entra duas vezes, como o comprovante: mesmo arquivo ou
mesma linha digitavel de um boleto que nao foi descartado.
"""

import hashlib
import logging
from datetime import date

from django.utils import timezone

from app.models import Boleto
from app.services.comprovantes.arquivo import preparar_arquivo
from app.services.comprovantes.normalizar import valor_decimal

from .leitura import ler_boleto
from .leitura_gratis import ler_sem_ia
from .linha_digitavel import ler_linha

logger = logging.getLogger(__name__)


class BoletoRepetido(Exception):
    """Este boleto ja esta no sistema (a pagar ou pago)."""


def _data(texto):
    try:
        return date.fromisoformat(str(texto))
    except ValueError:
        return None


def _vivos(conta):
    """Os boletos que contam para repetido: descartado por engano pode voltar."""
    return Boleto.objects.filter(conta=conta).exclude(situacao=Boleto.Situacao.DESCARTADO)


def registrar_boleto(
    conta,
    conteudo,
    tipo,
    nome,
    *,
    origem,
    loja=None,
    vencimento=None,
    valor=None,
    dispositivo=None,
    enviado_por=None,
    ler=ler_boleto,
    ler_gratis=ler_sem_ia,
    hoje=None,
):
    """Grava o boleto e o completa com a leitura.

    ArquivoInvalido e BoletoRepetido saem sem deixar nada gravado.
    """
    hoje = hoje or timezone.localdate()
    arquivo_hash = hashlib.sha256(conteudo).hexdigest()
    if _vivos(conta).filter(arquivo_hash=arquivo_hash).exists():
        raise BoletoRepetido()

    arquivo, arquivo_tipo = preparar_arquivo(conteudo, tipo)

    digitados = {"vencimento": vencimento, "valor": valor}
    boleto = Boleto.objects.create(
        conta=conta,
        loja=loja,
        origem=origem,
        vencimento=vencimento,
        valor=valor,
        arquivo=arquivo,
        arquivo_tipo=arquivo_tipo,
        arquivo_nome=nome,
        arquivo_hash=arquivo_hash,
        dispositivo=dispositivo,
        enviado_por=enviado_por,
        campos_editados=[campo for campo, dado in digitados.items() if dado is not None],
    )

    try:
        linha = ler_gratis(conteudo, tipo)
    except Exception as erro:
        logger.info("Boleto %s: leitura sem IA falhou: %s", boleto.public_id, erro)
        linha = None
    if linha:
        _usar_linha(boleto, linha, hoje)

    # Com a linha e o vencimento, a IA nao acrescenta o que falta saber.
    if not (boleto.linha_digitavel and boleto.vencimento):
        try:
            lido = ler(arquivo, arquivo_tipo)
        except Exception as erro:
            # A foto e o vencimento digitado ja estao guardados: a leitura que
            # falha nao pode custar o boleto.
            logger.warning("Boleto %s sem leitura: %s", boleto.public_id, erro)
        else:
            _mesclar(boleto, lido, hoje)

    if boleto.linha_digitavel and (
        _vivos(conta).filter(linha_digitavel=boleto.linha_digitavel).exclude(pk=boleto.pk).exists()
    ):
        boleto.delete()
        raise BoletoRepetido()

    if not boleto.linha_digitavel and _parecido_com_outro(boleto):
        boleto.conferir = True

    boleto.save()
    return boleto


def _parecido_com_outro(boleto):
    """Mesmo vencimento e valor de outro, sem linha para ter certeza: talvez
    seja outra foto do mesmo boleto. Nao recusa — a gerencia olha."""
    if boleto.vencimento is None or boleto.valor is None:
        return False
    return (
        _vivos(boleto.conta_id)
        .filter(vencimento=boleto.vencimento, valor=boleto.valor)
        .exclude(pk=boleto.pk)
        .exists()
    )


def _preencher(boleto, campo, lido):
    """O lido completa o vazio; se discorda do digitado, a gerencia confere."""
    if lido is None:
        return
    atual = getattr(boleto, campo)
    if atual is None:
        setattr(boleto, campo, lido)
    elif atual != lido:
        boleto.conferir = True


def _usar_linha(boleto, digitos, hoje):
    """Linha valida: guarda e tira dela vencimento e valor. Devolve se valeu."""
    linha = ler_linha(digitos, hoje)
    if linha is None:
        return False
    boleto.linha_digitavel = linha.digitos
    _preencher(boleto, "vencimento", linha.vencimento)
    _preencher(boleto, "valor", linha.valor)
    return True


def _mesclar(boleto, lido, hoje):
    boleto.leitura_bruta = lido
    # Nao e boleto, ou e carne: le o primeiro, e a gerencia abre a foto para
    # ver o resto. As outras parcelas nao podem passar despercebidas.
    if lido.get("e_boleto") is False or lido.get("varios_boletos") is True:
        boleto.conferir = True

    # A conta exata vale mais que o que o modelo leu da foto: a linha entra
    # primeiro, e o lido so completa o que ela nao trouxe.
    if lido.get("linha_digitavel") and not boleto.linha_digitavel:
        if not _usar_linha(boleto, lido["linha_digitavel"], hoje):
            # Digito trocado na leitura: copiar essa linha no banco daria erro.
            boleto.conferir = True

    lido_vencimento = _data(lido.get("vencimento")) if lido.get("vencimento") else None
    if boleto.linha_digitavel and boleto.vencimento is not None:
        lido_vencimento = None
    _preencher(boleto, "vencimento", lido_vencimento)
    if not boleto.linha_digitavel or boleto.valor is None:
        _preencher(boleto, "valor", valor_decimal(lido.get("valor")))

    boleto.beneficiario_nome = (lido.get("beneficiario_nome") or "")[:200]
    boleto.beneficiario_documento = (lido.get("beneficiario_documento") or "")[:30]
