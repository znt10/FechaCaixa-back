"""Leitura do XML da NFe — texto entra, dados saem.

Nao importa Django de proposito: quem le a nota nao precisa saber que existe
banco, e assim o teste do parser roda contra dezenas de XMLs sem tocar em
tabela nenhuma.

A leitura e do XML e nao do PDF porque o DANFE e layout impresso: extrair
valor e CNPJ dele exige OCR, erra em fornecedor com layout diferente, e erra
em silencio — entra valor errado no relatorio e ninguem percebe.
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree
from xml.parsers import expat

# Toda NFe usa este namespace, em qualquer versao do layout.
NS = {"nfe": "http://www.portalfiscal.inf.br/nfe"}


class XmlNaoEhNFe(Exception):
    """O arquivo nao e uma NFe legivel: corrompido, ou o anexo errado."""


@dataclass(frozen=True)
class DadosDaNota:
    chave: str
    numero: str
    serie: str
    data_emissao: datetime.date
    valor_total: Decimal
    cnpj_emitente: str
    nome_emitente: str
    # None quando a nota foi emitida para CPF: nao ha CNPJ para casar com loja.
    cnpj_destinatario: str | None


def _texto(no, caminho):
    achado = no.find(caminho, NS) if no is not None else None
    if achado is None or achado.text is None:
        return None
    return achado.text.strip()


def _data(texto):
    """dhEmi (4.00, com hora e fuso) ou dEmi (3.10, so a data)."""
    if not texto:
        raise XmlNaoEhNFe("A nota nao informa a data de emissao.")
    try:
        return datetime.date.fromisoformat(texto[:10])
    except ValueError as erro:
        raise XmlNaoEhNFe(f"Data de emissao ilegivel: {texto}") from erro


def _recusar_se_for_perigoso(xml_texto):
    """Recusa o XML que traz DOCTYPE, antes de qualquer parse de verdade.

    NFe da SEFAZ nunca tem DOCTYPE. Quem tem e a bomba de entidades: um
    punhado de entidades aninhadas cabe em algumas centenas de bytes, passa
    folgado pelo teto de tamanho do upload, e expande para gigabytes na
    memoria no momento do parse. Barrar DOCTYPE nao custa nada a quem sobe
    nota de verdade, e e a unica coisa que barra a bomba antes de ela crescer.

    Quem detecta e o `StartDoctypeDeclHandler` do expat — API publica e
    documentada, que dispara na declaracao DOCTYPE e antes de o parser
    expandir entidade nenhuma.

    O detalhe que ja custou um bypass nesta funcao: a guarda tem que parsear
    EXATAMENTE a mesma coisa que o parse de verdade vai parsear. A versao
    anterior alimentava o expat com `xml_texto.encode("utf-8")` (bytes) e
    deixava o `ElementTree.fromstring` receber `xml_texto` (str). Sao duas
    leituras diferentes do mesmo arquivo: com bytes o expat autodetecta a
    codificacao pelo padrao dos primeiros bytes e checa a coerencia com a
    declaracao do XML; com str ele pula essa checagem. Uma bomba salva em
    UTF-16LE e declarando `encoding="UTF-8"` era barrada por essa checagem de
    coerencia, ou seja, pelo motivo errado — a guarda condenava um documento
    que o parser real nem enxergava assim. Passando a str, o proprio handler
    de DOCTYPE pega o mesmo arquivo, e a guarda passa a olhar byte a byte o
    que o parser vai olhar. Medido: em str, as quatro variantes da bomba
    (utf-8 literal, utf-16le disfarcada de utf-8, utf-16le sem declaracao e
    utf-16le declarando utf-16) chamam o handler.

    O preco e o XML ser parseado duas vezes, aqui e no `fromstring`. E de
    proposito, e nao vale "otimizar": NFe chega em lote de dezenas de arquivos
    de dezenas de KB, entao a segunda passada custa milissegundos, e a unica
    forma de economiza-la seria fazer a guarda confiar no que o parser real
    reporta — que e voltar a ter guarda e parser olhando coisas diferentes,
    exatamente o bug que esta funcao existe para nao ter.
    """
    parser = expat.ParserCreate()

    def _achou_doctype(*_args):
        raise XmlNaoEhNFe(
            "O arquivo tem uma declaracao DOCTYPE, que uma NFe nunca tem."
        )

    parser.StartDoctypeDeclHandler = _achou_doctype
    try:
        parser.Parse(xml_texto, True)
    except expat.ExpatError as erro:
        # XML malformado: o parse de verdade, logo abaixo, daria o mesmo
        # veredito de qualquer forma.
        raise XmlNaoEhNFe("O arquivo nao e um XML valido.") from erro
    except UnicodeEncodeError as erro:
        # O pyexpat converte a str para utf-8 por dentro, e str com surrogate
        # nao converte. A view nunca produz uma dessas (o decode estrito nao
        # gera surrogate), mas este modulo promete "texto entra, dados saem,
        # ou XmlNaoEhNFe" — e a promessa vale para quem chamar o modulo
        # direto tambem.
        raise XmlNaoEhNFe("O arquivo nao e um XML valido.") from erro


def ler_nfe(xml_texto):
    """Os campos da nota, ou XmlNaoEhNFe se o arquivo nao for uma."""
    _recusar_se_for_perigoso(xml_texto)

    try:
        raiz = ElementTree.fromstring(xml_texto)
    except ElementTree.ParseError as erro:
        raise XmlNaoEhNFe("O arquivo nao e um XML valido.") from erro

    # Serve para nfeProc (com protocolo), NFe (cru) e infNFe solto: procurar o
    # infNFe em qualquer profundidade cobre os tres sem tres caminhos.
    inf = raiz.find(".//nfe:infNFe", NS)
    if inf is None and raiz.tag == f"{{{NS['nfe']}}}infNFe":
        inf = raiz
    if inf is None:
        raise XmlNaoEhNFe("O arquivo e um XML, mas nao e uma nota fiscal.")

    chave = (inf.get("Id") or "").removeprefix("NFe")
    if len(chave) != 44 or not chave.isdigit():
        raise XmlNaoEhNFe("A nota nao tem chave de acesso valida.")

    cnpj_emitente = _texto(inf, "nfe:emit/nfe:CNPJ")
    if not cnpj_emitente:
        raise XmlNaoEhNFe("A nota nao informa o CNPJ de quem emitiu.")

    bruto = _texto(inf, "nfe:total/nfe:ICMSTot/nfe:vNF")
    try:
        valor_total = Decimal(bruto)
    except (InvalidOperation, TypeError) as erro:
        raise XmlNaoEhNFe("A nota nao informa um valor total legivel.") from erro

    return DadosDaNota(
        chave=chave,
        numero=_texto(inf, "nfe:ide/nfe:nNF") or "",
        serie=_texto(inf, "nfe:ide/nfe:serie") or "",
        data_emissao=_data(
            _texto(inf, "nfe:ide/nfe:dhEmi") or _texto(inf, "nfe:ide/nfe:dEmi")
        ),
        valor_total=valor_total,
        cnpj_emitente=cnpj_emitente,
        nome_emitente=_texto(inf, "nfe:emit/nfe:xNome") or "",
        # Ausente quando o destinatario e CPF — e ai a nota nao e de loja nenhuma.
        cnpj_destinatario=_texto(inf, "nfe:dest/nfe:CNPJ"),
    )
