"""Importacao de um extrato: do arquivo ate as linhas no banco.

Nao conhece HTTP, como app.services.notas: quem chama pode ser a view do
upload hoje e a busca diaria na API do banco amanha.

A regra central mora aqui: subir de novo um periodo que ja entrou so
acrescenta o que e novo. O PicPay so exporta de 30 em 30 dias, entao o arquivo
de cada sabado repete o comeco do mes — e nada pode entrar duas vezes.
"""

import hashlib
from collections import Counter
from dataclasses import dataclass

from django.db import transaction

from app.models import ImportacaoDeExtrato, TransacaoBancaria

from .classificar import aplicar_regras, parear_transferencias
from .linhas import RecusaDeExtrato, assinatura_da_descricao, normalizar_descricao
from .ofx import decodificar, ler_ofx


@dataclass
class ResultadoDaImportacao:
    importacao: ImportacaoDeExtrato
    novas: int
    repetidas: int
    pareadas: int
    classificadas: int


def chaves_das_linhas(linhas):
    """A chave de repeticao de cada linha, na ordem em que vieram.

    A chave e a impressao digital (data, valor, descricao normalizada) mais
    QUAL repeticao dela a linha e dentro do arquivo: a primeira vira
    `impressao#1`, a segunda igual `impressao#2`.

    O numero da repeticao existe por causa de dois Pix de R$ 10 do mesmo
    cliente no mesmo dia. Sem ele, o segundo teria a mesma chave do primeiro
    e seria descartado como repetido — e R$ 10 sumiriam do extrato. Com ele,
    o arquivo que traz os dois entra com os dois; o proximo arquivo com os
    mesmos dois nao acrescenta nada; e um que traga um terceiro acrescenta so
    o `#3`.

    O FITID do OFX nao entra: ha banco que gera um novo a cada exportacao do
    mesmo periodo, e com ele na chave o segundo sabado duplicaria o mes todo.
    """
    vistas = Counter()
    chaves = []
    for linha in linhas:
        base = (
            f"{linha.data.isoformat()}|{linha.valor:.2f}|"
            f"{normalizar_descricao(linha.descricao)}"
        )
        impressao = hashlib.sha256(base.encode("utf-8")).hexdigest()
        vistas[impressao] += 1
        chaves.append(f"{impressao}#{vistas[impressao]}")
    return chaves


def _digitos_da_conta(texto):
    return "".join(c for c in (texto or "") if c.isdigit()).lstrip("0")


def conferir_a_conta(conta_bancaria, conta_no_arquivo):
    """Recusa o extrato de outra conta subido nesta.

    E o erro mais provavel da tela: a gerente baixa os extratos das oito lojas
    e sobe o da loja errada. Tolerante de proposito, porque cada banco escreve
    o numero de um jeito (com agencia na frente, sem o digito, com zeros): so
    recusa quando nenhum dos dois numeros contem o outro.
    """
    no_arquivo = _digitos_da_conta(conta_no_arquivo)
    cadastrado = _digitos_da_conta(conta_bancaria.numero)
    if not no_arquivo or not cadastrado:
        return
    if cadastrado in no_arquivo or no_arquivo in cadastrado:
        return
    raise RecusaDeExtrato(
        f"Este extrato é da conta {conta_no_arquivo}, e não da conta "
        f"{conta_bancaria.numero} ({conta_bancaria.loja.nome_loja}). Confira se "
        f"escolheu a conta certa."
    )


def ler_arquivo(nome_do_arquivo, conteudo):
    """O ExtratoLido, escolhendo o leitor pelo conteudo e nao so pelo nome.

    Por enquanto so OFX. O CSV do PicPay entra aqui quando houver um arquivo
    de exemplo para o leitor ser escrito contra ele.
    """
    comeco = conteudo[:4096]
    if isinstance(comeco, bytes):
        comeco = comeco.decode("latin-1")
    if "OFX" in comeco.upper() or nome_do_arquivo.lower().endswith(".ofx"):
        return ler_ofx(conteudo)
    raise RecusaDeExtrato(
        "Por enquanto o sistema lê extratos em OFX. No internet banking, "
        "exporte o extrato escolhendo o formato OFX."
    )


@transaction.atomic
def importar_extrato(conta_bancaria, nome_do_arquivo, conteudo, usuario=None):
    """Grava o que o arquivo tem de novo e classifica o que der sozinho.

    Nunca apaga nem regrava o que ja existe: a classificacao que a gerente ja
    deu as linhas antigas nao pode se perder porque o periodo foi subido de
    novo.
    """
    if not conta_bancaria.ativo:
        raise RecusaDeExtrato(
            "Esta conta bancária está desativada. Ative a conta antes de "
            "subir o extrato dela."
        )

    extrato = ler_arquivo(nome_do_arquivo, conteudo)
    conferir_a_conta(conta_bancaria, extrato.conta_no_arquivo)

    chaves = chaves_das_linhas(extrato.linhas)
    ja_existem = set(
        TransacaoBancaria.objects.filter(
            conta_bancaria=conta_bancaria, chave__in=chaves
        ).values_list("chave", flat=True)
    )

    conta = conta_bancaria.loja.conta
    # A mesma decodificacao do leitor: um OFX em UTF-8 com acento guardado
    # como latin-1 viraria "JoÃ£o" justo na copia que serve de prova.
    texto = decodificar(conteudo)
    importacao = ImportacaoDeExtrato.objects.create(
        conta_bancaria=conta_bancaria,
        nome_do_arquivo=nome_do_arquivo[:255],
        conteudo_bruto=texto,
        enviada_por=usuario,
        periodo_de=extrato.periodo_de,
        periodo_ate=extrato.periodo_ate,
    )

    novas = [
        TransacaoBancaria(
            conta=conta,
            conta_bancaria=conta_bancaria,
            importacao=importacao,
            data=linha.data,
            valor=linha.valor,
            descricao=linha.descricao,
            id_do_banco=linha.id_do_banco,
            chave=chave,
            assinatura=assinatura_da_descricao(linha.descricao),
        )
        for linha, chave in zip(extrato.linhas, chaves)
        if chave not in ja_existem
    ]
    TransacaoBancaria.objects.bulk_create(novas)

    pareadas = classificadas = 0
    if novas:
        datas = [transacao.data for transacao in novas]
        pareadas = parear_transferencias(conta, min(datas), max(datas))
        # Relidas do banco e nao as da lista: no MySQL o bulk_create nao
        # devolve o id, e o pareamento acabou de mudar algumas delas.
        classificadas = aplicar_regras(
            conta, list(TransacaoBancaria.objects.filter(importacao=importacao))
        )

    importacao.novas = len(novas)
    importacao.repetidas = len(extrato.linhas) - len(novas)
    importacao.save(update_fields=["novas", "repetidas"])

    return ResultadoDaImportacao(
        importacao=importacao,
        novas=importacao.novas,
        repetidas=importacao.repetidas,
        pareadas=pareadas,
        classificadas=classificadas,
    )
