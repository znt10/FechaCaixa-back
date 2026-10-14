"""A leitura do comprovante pelo Claude Haiku 5.5.

Uma chamada por arquivo, com structured outputs: a resposta e sempre um JSON
no esquema abaixo. Testado em 13/10/2026 com 3 comprovantes reais (Nubank e
PicPay): acertou todos os campos, a US$ 0,0004 por comprovante.
"""

import base64
import json
import os

import anthropic
from django.conf import settings

CAMPOS = [
    "valor",
    "data_hora",
    "id_transacao",
    "pagador_nome",
    "pagador_documento",
    "pagador_banco",
    "pagador_agencia",
    "pagador_conta",
    "recebedor_nome",
    "recebedor_documento",
    "recebedor_banco",
]

_TEXTO_OU_NULO = {"anyOf": [{"type": "string"}, {"type": "null"}]}

ESQUEMA = {
    "type": "object",
    "properties": {
        "e_comprovante": {"type": "boolean"},
        **{campo: _TEXTO_OU_NULO for campo in CAMPOS},
    },
    "required": ["e_comprovante", *CAMPOS],
    "additionalProperties": False,
}

PROMPT = """Este arquivo deve ser um comprovante de pagamento brasileiro (Pix, TED, boleto, lotérica).
Extraia os campos exatamente como aparecem. Use null para o que não aparece no comprovante.
- valor: só número com ponto decimal, ex. "129.80".
- data_hora: ISO 8601 sem fuso, ex. "2026-10-13T10:03:22".
- pagador = quem pagou (origem, "De"); recebedor = quem recebeu (destino, "Para").
- documento: CPF ou CNPJ como aparece, inclusive mascarado.
- agencia e conta: só se aparecerem.
- e_comprovante: false se o arquivo não for um comprovante de pagamento."""


class LeituraIndisponivel(Exception):
    """A leitura nao aconteceu. O comprovante entra em "conferir" com o arquivo."""


def ler_arquivo(conteudo, tipo):
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise LeituraIndisponivel("leitura desligada")

    dados = base64.standard_b64encode(conteudo).decode()
    if tipo == "application/pdf":
        bloco = {
            "type": "document",
            "source": {"type": "base64", "media_type": tipo, "data": dados},
        }
    else:
        bloco = {
            "type": "image",
            "source": {"type": "base64", "media_type": tipo, "data": dados},
        }

    try:
        resposta = anthropic.Anthropic(timeout=60.0).messages.create(
            model=settings.COMPROVANTES_MODELO,
            max_tokens=2000,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": ESQUEMA},
            },
            messages=[{"role": "user", "content": [bloco, {"type": "text", "text": PROMPT}]}],
        )
    except anthropic.APIError as erro:
        # APIError cobre conexao, timeout e status (429, 5xx); o SDK ja tentou
        # de novo duas vezes antes de chegar aqui.
        raise LeituraIndisponivel(f"a leitura falhou ({type(erro).__name__})") from erro

    if resposta.stop_reason != "end_turn":
        # "refusal" e "max_tokens" nao garantem JSON valido no esquema.
        raise LeituraIndisponivel(f"a leitura parou antes do fim ({resposta.stop_reason})")

    texto = next((bloco.text for bloco in resposta.content if bloco.type == "text"), "")
    try:
        return json.loads(texto)
    except json.JSONDecodeError as erro:
        raise LeituraIndisponivel("a leitura veio incompleta") from erro
