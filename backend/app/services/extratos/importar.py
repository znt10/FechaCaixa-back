"""Importacao de um extrato: do arquivo ate as linhas no banco.

Nao conhece HTTP, como app.services.notas: quem chama pode ser a view do
upload hoje e a busca diaria na API do banco amanha.

A regra central mora aqui: subir de novo um periodo que ja entrou so
acrescenta o que e novo. O PicPay so exporta de 30 em 30 dias, entao o arquivo
de cada sabado repete o comeco do mes — e nada pode entrar duas vezes. Nem o
que a API do banco ja gravou: a linha do arquivo adota a dela (ver api.py).
"""

import hashlib
from collections import Counter
from dataclasses import dataclass

from django.db import transaction

from app.models import ImportacaoDeExtrato, TransacaoBancaria

from .categorias import garantir_categorias_de_movimento
from .classificar import aplicar_regras, marcar_entradas_como_venda, parear_transferencias
from .csv_picpay import e_csv_do_picpay, ler_csv_picpay
from .linhas import RecusaDeExtrato, assinatura_da_descricao, normalizar_descricao
from .ofx import decodificar, ler_ofx


@dataclass
class ResultadoDaImportacao:
    # None quando a API nao trouxe linha nova: so o arquivo cria sempre.
    importacao: ImportacaoDeExtrato | None
    novas: int
    repetidas: int
    pareadas: int
    classificadas: int
    # Entradas que viraram venda sozinhas: contadas a parte, porque
    # "classificadas" e o que a gerente ja tinha respondido.
    vendas: int = 0
    # Das repetidas, as que ja estavam no extrato pela outra fonte (API ou
    # arquivo) e ganharam aqui a identidade desta.
    adotadas: int = 0


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

    OFX de qualquer banco, e o CSV do PicPay, que nao exporta OFX.
    """
    comeco = conteudo[:4096]
    if isinstance(comeco, bytes):
        comeco = comeco.decode("latin-1")
    if e_csv_do_picpay(comeco):
        return ler_csv_picpay(conteudo)
    if "OFX" in comeco.upper() or nome_do_arquivo.lower().endswith(".ofx"):
        return ler_ofx(conteudo)
    raise RecusaDeExtrato(
        "O sistema lê extratos em OFX e o CSV do PicPay. No internet banking, "
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
    # A chave pode estar em `chave`, na linha que nasceu de arquivo, ou em
    # `chave_do_arquivo`, na linha que nasceu da API e um arquivo ja adotou.
    da_conta = TransacaoBancaria.objects.filter(conta_bancaria=conta_bancaria)
    ja_existem = set(
        da_conta.filter(chave__in=chaves).values_list("chave", flat=True)
    ) | set(
        da_conta.filter(chave_do_arquivo__in=chaves).values_list("chave_do_arquivo", flat=True)
    )
    a_gravar = [
        (linha, chave)
        for linha, chave in zip(extrato.linhas, chaves)
        if chave not in ja_existem
    ]
    adotadas = adotar_linhas_da_api(conta_bancaria, a_gravar)

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
        for linha, chave in a_gravar
        if chave not in adotadas
    ]
    TransacaoBancaria.objects.bulk_create(novas)

    # Relidas do banco e nao as da lista: no MySQL o bulk_create nao devolve
    # o id.
    pareadas, classificadas, vendas = classificar_o_que_entrou(
        conta_bancaria, list(TransacaoBancaria.objects.filter(importacao=importacao))
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
        vendas=vendas,
        adotadas=len(adotadas),
    )


def adotar_linhas_da_api(conta_bancaria, linhas_e_chaves):
    """As linhas do arquivo que a API ja gravou. Devolve as chaves adotadas.

    A candidata e a linha que nasceu da API e ainda nao tem par de arquivo, do
    mesmo dia e valor; quem fica com qual decide `escolher_adotadas`. Adotar e
    so guardar a chave do arquivo nela, para o proximo arquivo com a mesma
    linha tambem a achar; o resto fica como a API gravou.
    """
    # Aqui e nao no topo: api.py importa deste modulo.
    from .api import PREFIXO_DA_CHAVE, escolher_adotadas, mesma_contraparte

    linhas = [linha for linha, _ in linhas_e_chaves]
    escolhidas = escolher_adotadas(
        linhas,
        TransacaoBancaria.objects.filter(
            conta_bancaria=conta_bancaria,
            id_externo__isnull=False,
            chave_do_arquivo__isnull=True,
            # Nascida da API. A linha que nasceu de arquivo e a API adotou
            # tambem tem id e nenhuma chave_do_arquivo, mas a impressao digital
            # do arquivo dela ja esta em `chave`: casar outra linha de arquivo
            # com ela deixaria uma linha da API livre para engolir um Pix novo.
            chave__startswith=PREFIXO_DA_CHAVE,
            data__in={linha.data for linha in linhas},
        ),
        lambda linha, candidata: mesma_contraparte(
            linha.descricao, candidata.contraparte_nome, candidata.contraparte_documento
        ),
    )
    adotadas = []
    for (_, chave), transacao in zip(linhas_e_chaves, escolhidas):
        if transacao is not None:
            transacao.chave_do_arquivo = chave
            adotadas.append(transacao)
    TransacaoBancaria.objects.bulk_update(adotadas, ["chave_do_arquivo"])
    return {transacao.chave_do_arquivo for transacao in adotadas}


def classificar_o_que_entrou(conta_bancaria, transacoes):
    """Pareia, aplica as regras e marca as vendas. Devolve as tres contagens.

    `transacoes` e o que acabou de entrar no extrato, relido do banco: as
    linhas novas do arquivo, ou as novas e as adotadas da API — a linha do
    arquivo que ganha a contraparte pela API pode parear agora.
    """
    conta = conta_bancaria.loja.conta
    # Antes do pareamento: ele cria a categoria de entre lojas, e com uma
    # categoria ja existindo a semeadura das outras (Vendas inclusa) nao
    # aconteceria mais.
    garantir_categorias_de_movimento(conta)
    pareadas = classificadas = 0
    if transacoes:
        datas = [transacao.data for transacao in transacoes]
        pareadas = parear_transferencias(conta, min(datas), max(datas))
        # Relidas de novo: o pareamento acabou de mudar algumas delas.
        classificadas = aplicar_regras(
            conta,
            list(TransacaoBancaria.objects.filter(pk__in=[t.pk for t in transacoes])),
        )
    # Por ultimo, e fora do `if transacoes`: toda entrada ainda pendente da
    # conta, e nao so as que entraram agora. O extrato subido antes desta regra
    # deixou as entradas esperando, e subir o mesmo arquivo de novo e o que as
    # resolve.
    vendas = marcar_entradas_como_venda(
        conta,
        list(
            TransacaoBancaria.objects.filter(
                conta_bancaria=conta_bancaria, tipo__isnull=True, par__isnull=True, valor__gt=0
            )
        ),
    )
    return pareadas, classificadas, vendas
