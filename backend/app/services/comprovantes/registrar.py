"""Um arquivo vira um comprovante gravado.

Nada se perde: se a leitura falha ou o arquivo e grande demais, o comprovante
entra em "conferir" com o motivo escrito. Devolve None so quando ele ja
existia (mesmo ID de transacao na empresa, ou o mesmo anexo do mesmo e-mail).
"""

import re

from django.db import IntegrityError, transaction

from app.models import Comprovante

from .arquivo import TAMANHO_MAXIMO, ArquivoInvalido, preparar_arquivo
from .categoria import categoria_sugerida
from .leitura import LeituraIndisponivel, ler_arquivo
from .loja import achar_loja
from .normalizar import data_hora, normalizar_nome, valor_decimal

OK = Comprovante.Situacao.OK
CONFERIR = Comprovante.Situacao.CONFERIR

TEXTOS = {
    "pagador_nome": 200,
    "pagador_documento": 30,
    "pagador_banco": 100,
    "pagador_agencia": 20,
    "pagador_conta": 30,
    "recebedor_nome": 200,
    "recebedor_documento": 30,
    "recebedor_banco": 100,
}


def registrar_comprovante(
    conta,
    conteudo,
    tipo,
    nome,
    *,
    origem,
    remetente="",
    email_message_id=None,
    indice_anexo=None,
    enviado_por=None,
    loja=None,
    ler=ler_arquivo,
):
    comprovante = Comprovante(
        conta=conta,
        origem=origem,
        situacao=CONFERIR,
        arquivo_nome=(nome or "")[:255],
        remetente=remetente,
        email_message_id=email_message_id,
        indice_anexo=indice_anexo,
        enviado_por=enviado_por,
        loja=loja,
    )

    if len(conteudo) > TAMANHO_MAXIMO:
        comprovante.motivo = "Arquivo grande demais (mais de 10 MB): não foi guardado."
        return _gravar(comprovante)

    try:
        arquivo, tipo_final = preparar_arquivo(conteudo, tipo)
    except ArquivoInvalido as erro:
        comprovante.motivo = str(erro)
        return _gravar(comprovante)
    comprovante.arquivo = arquivo
    comprovante.arquivo_tipo = tipo_final

    try:
        dados = ler(arquivo, tipo_final)
    except LeituraIndisponivel as erro:
        comprovante.motivo = f"Não deu para ler: {erro}."
        return _gravar(comprovante)

    _aplicar_leitura(comprovante, dados, loja_escolhida=loja)
    return _gravar(comprovante)


def _aplicar_leitura(comprovante, dados, loja_escolhida):
    comprovante.leitura_bruta = dados
    comprovante.valor = valor_decimal(dados.get("valor"))
    comprovante.data_hora = data_hora(dados.get("data_hora"))
    # Sem espaco nem quebra de linha: o PDF do PicPay quebra o ID em duas
    # linhas, e a leitura pode trazer o pedaco com ou sem espaco.
    comprovante.id_transacao = re.sub(r"\s+", "", dados.get("id_transacao") or "")[:64] or None
    for campo, limite in TEXTOS.items():
        setattr(comprovante, campo, (dados.get(campo) or "").strip()[:limite])
    comprovante.recebedor_normalizado = normalizar_nome(comprovante.recebedor_nome)[:200]

    conta = comprovante.conta
    loja_do_pagador = achar_loja(
        conta,
        comprovante.pagador_documento,
        comprovante.pagador_agencia,
        comprovante.pagador_conta,
    )
    # Loja do lado de quem recebeu, e nao de quem pagou: dinheiro entrando.
    recebido = loja_do_pagador is None and achar_loja(
        conta, comprovante.recebedor_documento
    ) is not None
    comprovante.loja = loja_escolhida or loja_do_pagador
    comprovante.elemento = categoria_sugerida(conta, comprovante.recebedor_normalizado)
    comprovante.motivo, comprovante.situacao = _situacao(comprovante, dados, recebido)


def _situacao(comprovante, dados, recebido):
    if dados.get("e_comprovante") is False:
        return "Não parece um comprovante de pagamento.", CONFERIR
    if comprovante.valor is None:
        return "Não deu para ler o valor.", CONFERIR
    if comprovante.data_hora is None:
        return "Não deu para ler a data.", CONFERIR
    if recebido:
        return "Parece Pix recebido: o dinheiro entrou na loja.", CONFERIR
    return "", OK


def _gravar(comprovante):
    if (
        comprovante.id_transacao
        and Comprovante.objects.filter(
            conta=comprovante.conta, id_transacao=comprovante.id_transacao
        ).exists()
    ):
        return None
    try:
        # Savepoint: a unicidade do banco e a guarda contra dois envios
        # simultaneos do mesmo ID; a consulta acima so evita o caminho comum.
        with transaction.atomic():
            comprovante.save()
    except IntegrityError:
        return None
    return comprovante
