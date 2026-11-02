"""Guardar um boleto: do arquivo ate a linha no banco.

Nao conhece HTTP, como o registro da nota: quem chama e o formulario da loja
ou o painel, e a regra mora aqui.

A ordem e a regra central. Grava ANTES de ler: o celular da loja pode tirar
uma foto que ninguem le, e a foto guardada com o vencimento digitado ja e o
que a gerencia precisa. Depois le, e a leitura so completa o que ficou
vazio — o que a loja digitou olhando o papel vale mais.
"""

import logging
from datetime import date

from django.utils import timezone

from app.models import Boleto
from app.services.comprovantes.arquivo import preparar_arquivo
from app.services.comprovantes.normalizar import valor_decimal

from .leitura import ler_boleto
from .linha_digitavel import ler_linha

logger = logging.getLogger(__name__)


def _data(texto):
    try:
        return date.fromisoformat(str(texto))
    except ValueError:
        return None


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
    hoje=None,
):
    """Grava o boleto e o completa com a leitura. ArquivoInvalido sai antes de gravar."""
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
        dispositivo=dispositivo,
        enviado_por=enviado_por,
        campos_editados=[campo for campo, dado in digitados.items() if dado is not None],
    )

    try:
        lido = ler(arquivo, arquivo_tipo)
    except Exception as erro:
        # A foto e o vencimento digitado ja estao guardados: a leitura que
        # falha nao pode custar o boleto.
        logger.warning("Boleto %s sem leitura: %s", boleto.public_id, erro)
        return boleto

    _mesclar(boleto, lido, hoje or timezone.localdate())
    boleto.save()
    return boleto


def _mesclar(boleto, lido, hoje):
    boleto.leitura_bruta = lido
    if lido.get("e_boleto") is False:
        boleto.conferir = True

    lido_vencimento = _data(lido.get("vencimento")) if lido.get("vencimento") else None
    lido_valor = valor_decimal(lido.get("valor"))

    if lido.get("linha_digitavel"):
        linha = ler_linha(lido["linha_digitavel"], hoje)
        if linha is None:
            # Digito trocado na leitura: copiar essa linha no banco daria erro.
            boleto.conferir = True
        else:
            boleto.linha_digitavel = linha.digitos
            # A conta exata vale mais que o que o modelo leu da foto.
            lido_vencimento = linha.vencimento or lido_vencimento
            lido_valor = linha.valor or lido_valor

    for campo, lido_campo in (("vencimento", lido_vencimento), ("valor", lido_valor)):
        if lido_campo is None:
            continue
        atual = getattr(boleto, campo)
        if atual is None:
            setattr(boleto, campo, lido_campo)
        elif atual != lido_campo:
            boleto.conferir = True

    boleto.beneficiario_nome = (lido.get("beneficiario_nome") or "")[:200]
    boleto.beneficiario_documento = (lido.get("beneficiario_documento") or "")[:30]
