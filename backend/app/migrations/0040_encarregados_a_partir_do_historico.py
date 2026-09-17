"""Cria os encarregados a partir dos nomes que ja foram digitados.

O formulario pedia o nome de quem lancava como texto livre. Dai para frente
ele passa a ser uma escolha numa lista — e no dia do deploy essa lista estaria
vazia: nenhuma loja conseguiria lancar o caixa ate alguem cadastrar as pessoas
a mao, em todas as contas.

Entao o cadastro nasce do proprio historico. Para cada conta, cada nome
distinto ja usado vira um Encarregado, e os fechamentos daquele nome apontam
para ele.

A comparacao ignora caixa, acento e espaco em volta: "Marina", "marina" e
"MARINA " sao a mesma pessoa, e a grafia que fica e a mais usada. O que ela
NAO resolve e apelido — "Mari" e um registro separado de "Marina", porque
adivinhar isso seria inventar dado. A dona ve os dois na tela da empresa e
desativa o que sobrar; e uma limpeza de minutos, e a alternativa (juntar por
semelhanca) erraria calada.
"""

import unicodedata
from collections import Counter, defaultdict

from django.db import migrations


def chave(nome):
    """O nome sem acento, sem caixa e sem espaco em volta."""
    sem_acento = "".join(
        letra
        for letra in unicodedata.normalize("NFD", nome)
        if unicodedata.category(letra) != "Mn"
    )
    return " ".join(sem_acento.lower().split())


def criar_encarregados(apps, schema_editor):
    FechamentoCaixa = apps.get_model("app", "FechamentoCaixa")
    Encarregado = apps.get_model("app", "Encarregado")

    # (conta, chave) -> Counter das grafias, para escolher a mais usada.
    grafias = defaultdict(Counter)
    lancamentos = FechamentoCaixa.objects.select_related("loja").only(
        "id", "nome_funcionario", "loja__conta_id"
    )
    for fechamento in lancamentos:
        nome = (fechamento.nome_funcionario or "").strip()
        if not nome:
            continue
        grafias[(fechamento.loja.conta_id, chave(nome))][nome] += 1

    for (conta_id, chave_do_nome), contagem in grafias.items():
        nome_escolhido = contagem.most_common(1)[0][0]
        encarregado = Encarregado.objects.create(
            conta_id=conta_id, nome=nome_escolhido, ativo=True
        )
        # Um UPDATE por pessoa: sao poucas pessoas por conta, e assim o vinculo
        # nao depende de reler os lancamentos um a um.
        for grafia in contagem:
            FechamentoCaixa.objects.filter(
                loja__conta_id=conta_id, nome_funcionario=grafia
            ).update(lancado_por=encarregado)


def desfazer(apps, schema_editor):
    """Desliga os fechamentos e apaga os encarregados criados aqui.

    So apaga quem nao tem consumo lancado: consumo nasce depois desta
    migracao, e apagar a pessoa levaria o valor junto.
    """
    FechamentoCaixa = apps.get_model("app", "FechamentoCaixa")
    Encarregado = apps.get_model("app", "Encarregado")

    FechamentoCaixa.objects.update(lancado_por=None)
    Encarregado.objects.filter(consumos__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0039_consumo_por_encarregado"),
    ]

    operations = [
        migrations.RunPython(criar_encarregados, desfazer),
    ]
