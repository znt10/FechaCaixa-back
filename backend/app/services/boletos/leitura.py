"""A leitura do boleto pelo Claude Haiku 5.5.

O mesmo desenho da leitura do comprovante: uma chamada por arquivo, com
structured outputs, e LeituraIndisponivel para tudo que nao devolve o JSON
inteiro. Aqui a leitura e bonus: a loja ja digitou o vencimento, e o que vem
daqui so completa o que ficou vazio.
"""

import base64
import json
import os

import anthropic
from django.conf import settings

from app.services.comprovantes.leitura import LeituraIndisponivel

CAMPOS = [
    "vencimento",
    "valor",
    "beneficiario_nome",
    "beneficiario_documento",
    "linha_digitavel",
]

_TEXTO_OU_NULO = {"anyOf": [{"type": "string"}, {"type": "null"}]}

ESQUEMA = {
    "type": "object",
    "properties": {
        "e_boleto": {"type": "boolean"},
        **{campo: _TEXTO_OU_NULO for campo in CAMPOS},
    },
    "required": ["e_boleto", *CAMPOS],
    "additionalProperties": False,
}

PROMPT = """Este arquivo deve ser um boleto ou uma conta brasileira (boleto bancário, conta de luz, água, telefone).
Extraia os campos exatamente como aparecem. Use null para o que não aparece.
- vencimento: a data de vencimento, ISO 8601, ex. "2026-11-20".
- valor: o valor do documento, só número com ponto decimal, ex. "230.00".
- beneficiario = quem está cobrando (cedente, empresa da conta); documento = CPF ou CNPJ como aparece.
- linha_digitavel: a sequência numérica acima do código de barras, como aparece.
- e_boleto: false se o arquivo não for boleto nem conta."""


def ler_boleto(conteudo, tipo):
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise LeituraIndisponivel("leitura desligada")

    dados = base64.standard_b64encode(conteudo).decode()
    tipo_do_bloco = "document" if tipo == "application/pdf" else "image"
    bloco = {
        "type": tipo_do_bloco,
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
        raise LeituraIndisponivel(f"a leitura falhou ({type(erro).__name__})") from erro

    if resposta.stop_reason != "end_turn":
        raise LeituraIndisponivel(f"a leitura parou antes do fim ({resposta.stop_reason})")

    texto = next((bloco.text for bloco in resposta.content if bloco.type == "text"), "")
    try:
        return json.loads(texto)
    except json.JSONDecodeError as erro:
        raise LeituraIndisponivel("a leitura veio incompleta") from erro
