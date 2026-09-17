"""Repor um dia que a loja esqueceu de fechar, pela tela de empresa.

O lancamento de caixa sempre teve uma porta so: o formulario da loja, sem
login, com a conta saindo do cookie do aparelho. Quando a loja esquece de
fechar um turno, o dia fica com buraco e ninguem consegue tapar — o formulario
so lanca hoje, e a janela de correcao de 20 minutos ja fechou faz tempo.

Este arquivo protege a segunda porta: o gerente logado, no painel, escolhendo
a data. E a mesma criacao (mesmo serializer, mesmas regras de turno), com tres
travas que so existem deste lado:

    escopo    -> a loja tem que ser da conta DELE, e nao a que o payload alega
    limite    -> no maximo 30 dias para tras (o teto de "nao e futuro" ja existia)
    duplicata -> turno que ja tem lancamento nao aceita outro por cima

As tres valem so aqui. O formulario da loja segue exatamente como estava: ele
manda o dia de hoje e nao tem por que ser reapertado por uma tela que ele nao
usa.
"""

from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase

from app.feriados import motivo_de_turno_unico
from app.models import Conta, Encarregado, FechamentoCaixa

from .fabricas import criar_loja, vincular_conta

URL = "/api/v1/fechamentos-caixa/"


def dia_comum_recente(conta, dias_atras=1):
    """Um dia passado, dentro dos 30, em que a loja abre manha E tarde.

    Nao da para fixar uma data no calendario como o resto da suite faz: o
    limite de 30 dias e contado a partir de hoje, entao uma data fixa sai da
    janela sozinha com o passar das semanas. Andar para tras pulando
    domingo e feriado mantem o teste falando de turno comum em qualquer dia
    que a suite rode.
    """
    dia = timezone.localdate() - timedelta(days=dias_atras)
    while motivo_de_turno_unico(dia, conta):
        dia -= timedelta(days=1)
    return dia


class LancamentoPeloPainelTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        self.loja = criar_loja(
            nome_loja="Loja Centro", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.ana = Encarregado.objects.create(
            nome="Ana Paula", conta=self.conta, pode_lancar_caixa=True
        )
        self.gerente = User.objects.create_user(
            username="gerente@aurora.com", email="gerente@aurora.com", password="x"
        )
        self.gerente.groups.add(Group.objects.get_or_create(name="Gerente")[0])
        vincular_conta(self.gerente, self.conta)
        self.client.force_authenticate(user=self.gerente)

    def payload(self, **extra):
        base = {
            "loja": str(self.loja.public_id),
            "lancado_por": str(self.ana.public_id),
            "data": str(dia_comum_recente(self.conta)),
            "periodo": "TARDE",
            "pix": "0.00",
            "cartao": "0.00",
            "dinheiro": "350.00",
            "link_pagamento": "0.00",
            "houve_retirada": False,
            "houve_devolucao": False,
            "houve_desperdicio": False,
        }
        base.update(extra)
        return base

    def test_gerente_lanca_um_dia_que_a_loja_esqueceu(self):
        dia = dia_comum_recente(self.conta)

        resp = self.client.post(URL, self.payload(data=str(dia)), format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        lancamento = FechamentoCaixa.objects.get()
        self.assertEqual(lancamento.data, dia)
        self.assertEqual(lancamento.loja, self.loja)

    def test_nao_lanca_na_loja_de_outra_empresa(self):
        """O payload carrega o public_id da loja, e o campo aceita qualquer
        loja ativa do banco. Sem esta trava, colar ali o id da empresa vizinha
        criava caixa dentro dela — o formulario da loja se protege pela conta
        do aparelho, e o painel nao tinha o equivalente.

        O payload e inteiro da vizinha, loja e encarregado: as checagens que ja
        existiam so comparam as pecas do payload UMA COM A OUTRA, entao um
        payload internamente coerente passava por todas elas. O que falta e
        comparar com a conta de quem esta logado.
        """
        vizinha = Conta.objects.create(nome="Concorrente")
        loja_da_vizinha = criar_loja(
            nome_loja="Loja Deles", cidade="Patos", endereco="Rua 9", conta=vizinha
        )
        gente_da_vizinha = Encarregado.objects.create(
            nome="Beto", conta=vizinha, pode_lancar_caixa=True
        )

        resp = self.client.post(
            URL,
            self.payload(
                loja=str(loja_da_vizinha.public_id),
                lancado_por=str(gente_da_vizinha.public_id),
            ),
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertFalse(FechamentoCaixa.objects.exists())

    def test_recusa_dia_alem_do_limite_de_trinta(self):
        """Repor dia esquecido e coisa de dias, nao de meses. Passados 30 dias
        o caixa daquele dia ja virou relatorio fechado, e digitar 2019 por
        engano no seletor de data nao pode virar lancamento."""
        antigo = timezone.localdate() - timedelta(days=31)

        resp = self.client.post(URL, self.payload(data=str(antigo)), format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("data", resp.data)
        self.assertFalse(FechamentoCaixa.objects.exists())

    def test_recusa_turno_que_ja_tem_lancamento(self):
        """Repor pressupoe buraco. Onde ja existe caixa lancado, um segundo
        envio nao corrige o primeiro: os dois ficam, e o painel soma os dois
        como se a loja tivesse vendido o dobro. Mudar valor de turno lancado e
        a correcao da tela de fechamentos, que e outra porta."""
        dia = dia_comum_recente(self.conta)
        self.client.post(URL, self.payload(data=str(dia)), format="json")

        resp = self.client.post(URL, self.payload(data=str(dia)), format="json")

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(FechamentoCaixa.objects.count(), 1)

    def test_turno_cancelado_libera_o_lugar(self):
        """Cancelar existe justamente para o turno voltar a aceitar envio.
        Se a trava de duplicata olhasse tambem os cancelados, desfazer um
        lancamento errado deixaria o dia impossivel de refazer."""
        dia = dia_comum_recente(self.conta)
        self.client.post(URL, self.payload(data=str(dia)), format="json")
        lancado = FechamentoCaixa.objects.get()
        lancado.cancelado_em = timezone.now()
        lancado.save(update_fields=["cancelado_em"])

        resp = self.client.post(URL, self.payload(data=str(dia)), format="json")

        self.assertEqual(resp.status_code, 201, resp.data)

    def test_funcionario_logado_nao_lanca(self):
        """Guarda da decisao de permissao: quem tem login de Funcionario
        confere o caixa e nao repoe dia nenhum. Trocar a regra da view por um
        `IsAuthenticated` solto abriria esta porta sem ninguem notar."""
        funcionario = User.objects.create_user(
            username="caixa@aurora.com", email="caixa@aurora.com", password="x"
        )
        funcionario.groups.add(Group.objects.get_or_create(name="Funcionario")[0])
        vincular_conta(funcionario, self.conta)
        self.client.force_authenticate(user=funcionario)

        resp = self.client.post(URL, self.payload(), format="json")

        self.assertEqual(resp.status_code, 403, resp.data)
        self.assertFalse(FechamentoCaixa.objects.exists())

    def test_turnos_do_dia_escolhido(self):
        """A tela pergunta ao servidor quais turnos existem na data escolhida
        — a lista de feriados mora la, e uma segunda copia no JavaScript
        ficaria para tras."""
        dia = dia_comum_recente(self.conta)

        resp = self.client.get(f"{URL}turnos/?data={dia}")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["periodos"], ["MANHA", "TARDE"])

    def test_repoe_sem_dizer_quem_lancou(self):
        """Quem repoe pelo painel nao estava no turno — nao ha "quem lancou"
        para escolher, e obrigar a escolher alguem poria no historico uma
        pessoa que nao fechou aquele caixa.

        `lancado_por` aponta para Encarregado, que e gente da loja SEM login; o
        gerente e um User. Sao cadastros diferentes de proposito, entao nao ha
        FK para carimbar — fica nulo.
        """
        resp = self.client.post(URL, self.payload(lancado_por=None), format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(FechamentoCaixa.objects.get().lancado_por)

    def test_grava_no_nome_o_gerente_que_repos(self):
        """O texto ao lado da FK e o unico lugar sem migracao onde cabe dizer
        de onde veio o lancamento. Sem isso o dia reposto fica em branco onde
        os outros mostram quem fechou o caixa."""
        self.gerente.first_name = "Marina"
        self.gerente.save(update_fields=["first_name"])

        self.client.post(URL, self.payload(lancado_por=None), format="json")

        nome = FechamentoCaixa.objects.get().nome_funcionario
        self.assertIn("Marina", nome)
        # A marca importa tanto quanto o nome: sem ela o dia reposto se passa
        # por um que a loja mandou.
        self.assertIn("painel", nome.lower())

    def test_o_nome_cabe_no_campo(self):
        """`nome_funcionario` tem 100 caracteres. Login com e-mail longo mais o
        sufixo estourava a coluna — e no MySQL isso e erro de banco, nao um 400
        explicando o que houve."""
        self.gerente.first_name = ""
        self.gerente.email = f"{'a' * 90}@empresa-com-nome-comprido.com.br"
        self.gerente.save(update_fields=["first_name", "email"])

        resp = self.client.post(URL, self.payload(lancado_por=None), format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertLessEqual(len(FechamentoCaixa.objects.get().nome_funcionario), 100)

    def test_turnos_de_empresa_que_fecha_uma_vez_por_dia(self):
        """A empresa de um fechamento por dia so tem o turno do dia inteiro.
        A regra ja existia, mas so enxergava a conta quando ela vinha do
        aparelho: pelo painel a mesma empresa recebia manha e tarde, e o
        proprio serializer recusaria os dois na hora de enviar."""
        self.conta.fechamentos_por_dia = 1
        self.conta.save(update_fields=["fechamentos_por_dia"])
        dia = dia_comum_recente(self.conta)

        resp = self.client.get(f"{URL}turnos/?data={dia}")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["periodos"], ["DIA"])


class FormularioDaLojaSegueIgualTests(APITestCase):
    """Guarda do escopo: as travas novas sao do painel, e so dele.

    O formulario da loja manda o dia de hoje e nao escolhe data — apertar os
    dois lados nao protegeria nada e mudaria o comportamento de quem ja usa.
    """

    def setUp(self):
        cache.clear()
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        self.loja = criar_loja(
            nome_loja="Loja Centro", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.ana = Encarregado.objects.create(
            nome="Ana Paula", conta=self.conta, pode_lancar_caixa=True
        )
        self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": self.conta.codigo_acesso, "apelido": "Caixa"},
            format="json",
        )

    def payload(self, dia, **extra):
        base = {
            "loja": str(self.loja.public_id),
            "lancado_por": str(self.ana.public_id),
            "data": str(dia),
            "periodo": "TARDE",
            "pix": "0.00",
            "cartao": "0.00",
            "dinheiro": "350.00",
            "link_pagamento": "0.00",
            "houve_retirada": False,
            "houve_devolucao": False,
            "houve_desperdicio": False,
        }
        base.update(extra)
        return base

    def test_a_loja_nao_ganhou_o_limite_de_trinta_dias(self):
        antigo = dia_comum_recente(self.conta, dias_atras=45)

        resp = self.client.post(URL, self.payload(antigo), format="json")

        self.assertEqual(resp.status_code, 201, resp.data)

    def test_a_loja_ainda_e_obrigada_a_dizer_quem_lancou(self):
        """Guarda do escopo: quem lanca pelo aparelho ESTA no turno, e o
        cadastro existe justamente para o consumo do mes fechar por pessoa.
        So o painel repoe sem escolher ninguem."""
        dia = dia_comum_recente(self.conta)
        corpo = self.payload(dia)
        corpo.pop("lancado_por")

        resp = self.client.post(URL, corpo, format="json")

        self.assertEqual(resp.status_code, 400, resp.data)

    def test_a_loja_nao_ganhou_a_trava_de_duplicata(self):
        dia = dia_comum_recente(self.conta)
        self.client.post(URL, self.payload(dia), format="json")

        resp = self.client.post(URL, self.payload(dia), format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(FechamentoCaixa.objects.count(), 2)
