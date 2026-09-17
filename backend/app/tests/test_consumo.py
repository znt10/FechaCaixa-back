"""O consumo: uma linha por pessoa, lancada pelo gerente junto com o caixa.

A dona controlava numa planilha a mao quanto cada pessoa consumia no mes, para
descontar depois. Isso ja morou dentro do FechamentoCaixa como um par de campos
(`houve_consumo` e `valor_consumo`), e o desenho so tinha lugar para um consumo
por turno, sempre da pessoa que lancou o caixa. Numa empresa de 60 funcionarios
com 20 lancando, as outras 40 nao apareciam em lugar nenhum.

Quem registra continua sendo o gerente, no mesmo envio do fechamento — nao e
cada pessoa que manda o seu. O que mudou e que ele diz de quem foi cada valor.

A regra que este arquivo mais protege continua sendo a que menos se ve olhando
a tela: consumo NAO e saida de caixa. A pessoa consumiu e nao pagou na hora,
entao nenhum dinheiro deixou a gaveta — se o total liquido encolher por causa
dele, o fechamento passa a acusar uma diferenca que nao existe.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.db.models import ProtectedError
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from app.models import Conta, Consumo, Encarregado, FechamentoCaixa

from .fabricas import DIA_COMUM, criar_loja, lista, vincular_conta

URL = "/api/v1/fechamentos-caixa/"


class BaseDoConsumo(APITestCase):
    """A empresa em miniatura: um gerente que fecha o caixa e gente que so come."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = APIClient()
        self.conta = Conta.objects.create(nome="Marina")
        self.loja = criar_loja(
            nome_loja="Loja Centro", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        # Os tres papeis que a tela da empresa separa.
        self.ana = Encarregado.objects.create(nome="Ana Paula", conta=self.conta)
        self.camila = Encarregado.objects.create(
            nome="Camila", conta=self.conta, pode_lancar_caixa=False
        )
        self.bruno = Encarregado.objects.create(
            nome="Bruno", conta=self.conta, pode_lancar_caixa=False
        )
        self.entrar()

    def entrar(self, conta=None):
        conta = conta or self.conta
        self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": conta.codigo_acesso, "apelido": "Caixa"},
            format="json",
        )

    def consumo(self, pessoa, valor):
        return {"encarregado": str(pessoa.public_id), "valor": valor}

    def payload(self, **extra):
        base = {
            "loja": str(self.loja.public_id),
            "lancado_por": str(self.ana.public_id),
            "data": DIA_COMUM,
            "periodo": "TARDE",
            "pix": "100.00",
            "cartao": "0.00",
            "dinheiro": "50.00",
            "link_pagamento": "0.00",
            "houve_retirada": False,
            "houve_despesa": False,
            "houve_desperdicio": False,
        }
        base.update(extra)
        return base


class LancarConsumoNoFechamentoTests(BaseDoConsumo):
    def test_gerente_lanca_o_consumo_de_cada_pessoa(self):
        resp = self.client.post(
            URL,
            self.payload(
                consumos=[
                    self.consumo(self.camila, "12.00"),
                    self.consumo(self.bruno, "8.50"),
                ]
            ),
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        registrados = {
            consumo.encarregado.nome: consumo.valor
            for consumo in Consumo.objects.select_related("encarregado")
        }
        self.assertEqual(
            registrados, {"Camila": Decimal("12.00"), "Bruno": Decimal("8.50")}
        )

    def test_quem_nao_fecha_o_caixa_aparece_no_consumo(self):
        """E o ponto da mudanca: o consumo era sempre de quem lancava o turno,
        entao 40 das 60 pessoas da empresa nao tinham onde aparecer."""
        resp = self.client.post(
            URL,
            self.payload(consumos=[self.consumo(self.camila, "12.00")]),
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(Consumo.objects.get().encarregado, self.camila)

    def test_a_mesma_pessoa_pode_aparecer_em_turnos_diferentes(self):
        """O total do mes e por pessoa, e ela come mais de uma vez no mes."""
        self.client.post(
            URL,
            self.payload(consumos=[self.consumo(self.camila, "12.00")]),
            format="json",
        )
        self.client.post(
            URL,
            self.payload(
                periodo="MANHA", consumos=[self.consumo(self.camila, "9.00")]
            ),
            format="json",
        )

        self.assertEqual(Consumo.objects.filter(encarregado=self.camila).count(), 2)

    def test_consumo_nao_mexe_no_caixa(self):
        """A regra silenciosa: ninguem pagou na hora, entao a gaveta nao mudou."""
        resp = self.client.post(
            URL,
            self.payload(consumos=[self.consumo(self.camila, "12.00")]),
            format="json",
        )

        fechamento = FechamentoCaixa.objects.get()
        self.assertEqual(fechamento.total, Decimal("150.00"))
        self.assertEqual(fechamento.registrado, 0)
        self.assertEqual(str(resp.data["total"]), "150.00")

    def test_turno_sem_consumo_nao_grava_nada(self):
        resp = self.client.post(URL, self.payload(), format="json")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(Consumo.objects.count(), 0)

    def test_a_confirmacao_devolve_o_que_foi_lancado(self):
        """A tela de confirmacao e o unico lugar onde o gerente reve o que
        digitou de cada pessoa — e e ele quem responde por esse numero."""
        resp = self.client.post(
            URL,
            self.payload(consumos=[self.consumo(self.camila, "12.00")]),
            format="json",
        )

        self.assertEqual(
            [(c["nome"], str(c["valor"])) for c in resp.data["consumos"]],
            [("Camila", "12.00")],
        )

    def test_valor_zerado_e_recusado(self):
        resp = self.client.post(
            URL,
            self.payload(consumos=[self.consumo(self.camila, "0.00")]),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_valor_negativo_e_recusado(self):
        resp = self.client.post(
            URL,
            self.payload(consumos=[self.consumo(self.camila, "-5.00")]),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_consumo_recusado_nao_deixa_o_fechamento_gravado(self):
        """Um envio so: o turno nao pode entrar sem o consumo que veio com ele,
        senao a gerencia desconta de uns e nao de outros."""
        self.client.post(
            URL,
            self.payload(consumos=[self.consumo(self.camila, "0.00")]),
            format="json",
        )

        self.assertEqual(FechamentoCaixa.objects.count(), 0)

    def test_pessoa_de_outra_conta_e_recusada(self):
        """O endpoint e publico: nada impede colar aqui o id da empresa vizinha."""
        vizinha = Conta.objects.create(nome="Aurora Salgados")
        de_fora = Encarregado.objects.create(nome="Carlos", conta=vizinha)

        resp = self.client.post(
            URL,
            self.payload(consumos=[self.consumo(de_fora, "12.00")]),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Consumo.objects.count(), 0)

    def test_quem_nao_consome_fica_fora_da_lista(self):
        """A marca de consumir e separada da de fechar o caixa: quem esta fora
        da lista na tela tambem esta fora no servidor."""
        sem_consumo = Encarregado.objects.create(
            nome="Estagiario", conta=self.conta, pode_consumir=False
        )

        resp = self.client.post(
            URL,
            self.payload(consumos=[self.consumo(sem_consumo, "12.00")]),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_pessoa_desativada_e_recusada(self):
        saiu = Encarregado.objects.create(
            nome="Quem saiu", conta=self.conta, ativo=False
        )

        resp = self.client.post(
            URL,
            self.payload(consumos=[self.consumo(saiu, "12.00")]),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_quem_nao_fecha_o_caixa_nao_lanca_o_turno(self):
        """A outra ponta da mesma separacao: Camila come, mas nao fecha."""
        resp = self.client.post(
            URL, self.payload(lancado_por=str(self.camila.public_id)), format="json"
        )

        self.assertEqual(resp.status_code, 400)

    def test_sem_aparelho_nao_lanca(self):
        anonimo = APIClient()

        resp = anonimo.post(
            URL,
            self.payload(consumos=[self.consumo(self.camila, "12.00")]),
            format="json",
        )

        self.assertIn(resp.status_code, (401, 403))
        self.assertEqual(Consumo.objects.count(), 0)

    def test_os_campos_antigos_nao_existem_mais(self):
        """Uma fonte so. Enquanto os dois caminhos existissem, o mesmo consumo
        podia ser lancado duas vezes sem ninguem perceber."""
        self.client.post(
            URL,
            self.payload(houve_consumo=True, valor_consumo="30.00"),
            format="json",
        )

        fechamento = FechamentoCaixa.objects.get()
        self.assertFalse(hasattr(fechamento, "houve_consumo"))
        self.assertFalse(hasattr(fechamento, "valor_consumo"))
        self.assertEqual(Consumo.objects.count(), 0)


class CorrigirOConsumoTests(BaseDoConsumo):
    """A janela de 20 minutos da loja tambem vale para o consumo: digitar 120
    no lugar de 12 e o erro que se percebe na hora, com a gaveta aberta."""

    def lancar(self, **extra):
        resp = self.client.post(URL, self.payload(**extra), format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        return FechamentoCaixa.objects.get(public_id=resp.data["id"])

    def url_de_correcao(self, fechamento):
        return f"{URL}{fechamento.public_id}/correcao/"

    def test_o_formulario_reabre_com_o_consumo_lancado(self):
        """Fechar a aba e voltar nao pode custar o que ja foi digitado."""
        fechamento = self.lancar(consumos=[self.consumo(self.camila, "12.00")])

        resp = self.client.get(self.url_de_correcao(fechamento))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            [(c["nome"], str(c["valor"])) for c in resp.data["consumos"]],
            [("Camila", "12.00")],
        )

    def test_corrigir_troca_a_lista_em_vez_de_somar_outra(self):
        fechamento = self.lancar(consumos=[self.consumo(self.camila, "120.00")])

        resp = self.client.patch(
            self.url_de_correcao(fechamento),
            self.payload(consumos=[self.consumo(self.camila, "12.00")]),
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Consumo.objects.count(), 1)
        self.assertEqual(Consumo.objects.get().valor, Decimal("12.00"))

    def test_corrigir_sem_falar_de_consumo_preserva_o_que_estava_la(self):
        """Consertar o PIX nao pode apagar o consumo de tabela."""
        fechamento = self.lancar(consumos=[self.consumo(self.camila, "12.00")])
        payload = self.payload(pix="180.00")
        payload.pop("consumos", None)

        self.client.patch(self.url_de_correcao(fechamento), payload, format="json")

        self.assertEqual(Consumo.objects.count(), 1)

    def test_lista_vazia_apaga_o_consumo(self):
        """Diferente de nao falar: aqui o gerente disse que nao teve nenhum."""
        fechamento = self.lancar(consumos=[self.consumo(self.camila, "12.00")])

        self.client.patch(
            self.url_de_correcao(fechamento), self.payload(consumos=[]), format="json"
        )

        self.assertEqual(Consumo.objects.count(), 0)

    def test_depois_da_janela_o_consumo_nao_muda_mais(self):
        fechamento = self.lancar(consumos=[self.consumo(self.camila, "12.00")])
        FechamentoCaixa.objects.filter(pk=fechamento.pk).update(
            created_at=timezone.now() - timedelta(minutes=21)
        )

        resp = self.client.patch(
            self.url_de_correcao(fechamento),
            self.payload(consumos=[self.consumo(self.camila, "99.00")]),
            format="json",
        )

        self.assertIn(resp.status_code, (403, 404))
        self.assertEqual(Consumo.objects.get().valor, Decimal("12.00"))


class ConsumoNoPainelTests(APITestCase):
    """O painel le o consumo aninhado no fechamento — e so o da propria conta."""

    def setUp(self):
        for nome in ("Admin", "Gerente", "Responsavel"):
            Group.objects.get_or_create(name=nome)

        self.conta = Conta.objects.create(nome="Marina")
        self.outra_conta = Conta.objects.create(nome="Aurora Salgados")

        self.gerente = User.objects.create_user(username="ger@x.com", password="123456")
        self.gerente.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(self.gerente, self.conta)

        self.gerente_alheio = User.objects.create_user(
            username="ger@aurora.com", password="123456"
        )
        self.gerente_alheio.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(self.gerente_alheio, self.outra_conta)

        self.loja = criar_loja(
            nome_loja="Loja A", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.camila = Encarregado.objects.create(nome="Camila", conta=self.conta)
        self.fechamento = FechamentoCaixa.objects.create(
            loja=self.loja, nome_funcionario="Ana", data=DIA_COMUM,
            periodo="TARDE", pix=100, cartao=0, dinheiro=0, link_pagamento=0,
        )
        self.consumo = Consumo.objects.create(
            fechamento=self.fechamento, encarregado=self.camila, valor="12.00"
        )

    def autenticado(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def linhas(self, resp):
        return lista(resp)

    def test_o_painel_ve_de_quem_foi_o_consumo(self):
        resp = self.autenticado(self.gerente).get(URL)

        consumos = self.linhas(resp)[0]["consumos"]
        self.assertEqual(
            [(c["nome"], str(c["valor"])) for c in consumos], [("Camila", "12.00")]
        )
        self.assertEqual(str(consumos[0]["encarregado"]), str(self.camila.public_id))

    def test_gerente_de_outra_conta_nao_ve(self):
        resp = self.autenticado(self.gerente_alheio).get(URL)

        self.assertEqual(len(self.linhas(resp)), 0)

    def test_apagar_quem_tem_consumo_e_bloqueado(self):
        """PROTECT: apagar a pessoa apagaria o valor que ela deve no mes."""
        with self.assertRaises(ProtectedError):
            self.camila.delete()

    def test_desativar_a_pessoa_preserva_o_consumo(self):
        """O caminho certo para quem saiu da empresa: o historico fica."""
        self.camila.ativo = False
        self.camila.save()

        self.assertEqual(Consumo.objects.count(), 1)

    def test_apagar_o_fechamento_leva_o_consumo_junto(self):
        """CASCADE de proposito: os dois vieram no mesmo envio, e um consumo
        sem o turno em que aconteceu nao tem loja, dia nem turno para exibir."""
        self.fechamento.delete()

        self.assertEqual(Consumo.objects.count(), 0)


class CorrigirOConsumoPeloPainelTests(APITestCase):
    """A correcao da gerencia, sem prazo — a outra ponta da janela de 20 minutos.

    A loja lanca o consumo na pessoa errada e so se descobre no fim do mes, na
    hora de descontar. Ate aqui o unico caminho era cancelar o turno inteiro e
    lancar de novo, o que jogava fora o caixa correto junto: o consumo e o
    unico dado do turno que a gerencia nao conseguia consertar.
    """

    def setUp(self):
        for nome in ("Admin", "Gerente", "Responsavel"):
            Group.objects.get_or_create(name=nome)

        self.conta = Conta.objects.create(nome="Marina")
        self.gerente = User.objects.create_user(username="ger@x.com", password="123456")
        self.gerente.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(self.gerente, self.conta)

        self.loja = criar_loja(
            nome_loja="Loja A", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.camila = Encarregado.objects.create(nome="Camila", conta=self.conta)
        self.bruno = Encarregado.objects.create(nome="Bruno", conta=self.conta)
        self.fechamento = FechamentoCaixa.objects.create(
            loja=self.loja, nome_funcionario="Ana", data=DIA_COMUM,
            periodo="TARDE", pix=100, cartao=0, dinheiro=0, link_pagamento=0,
        )

        self.client = APIClient()
        self.client.force_authenticate(self.gerente)

    def url(self):
        return f"{URL}{self.fechamento.public_id}/"

    def consumo(self, pessoa, valor):
        return {"encarregado": str(pessoa.public_id), "valor": valor}

    def lancado(self, pessoa, valor):
        return Consumo.objects.create(
            fechamento=self.fechamento, encarregado=pessoa, valor=valor
        )

    def registrados(self):
        return {
            consumo.encarregado.nome: consumo.valor
            for consumo in Consumo.objects.select_related("encarregado")
        }

    def test_gerencia_corrige_o_valor_consumido(self):
        """O erro classico: 120 no lugar de 12, percebido no fim do mes."""
        self.lancado(self.camila, "120.00")

        resp = self.client.patch(
            self.url(),
            {"consumos": [self.consumo(self.camila, "12.00")]},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self.registrados(), {"Camila": Decimal("12.00")})

    def test_gerencia_troca_a_pessoa_do_consumo(self):
        """Lancado na pessoa errada: quem come e quem paga sao a mesma linha."""
        self.lancado(self.camila, "12.00")

        resp = self.client.patch(
            self.url(),
            {"consumos": [self.consumo(self.bruno, "12.00")]},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self.registrados(), {"Bruno": Decimal("12.00")})

    def test_gerencia_lanca_consumo_em_turno_que_nao_tinha(self):
        """A loja esqueceu de lancar; a pessoa comeu do mesmo jeito."""
        resp = self.client.patch(
            self.url(),
            {"consumos": [self.consumo(self.camila, "9.00")]},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self.registrados(), {"Camila": Decimal("9.00")})

    def test_corrigir_outro_campo_preserva_o_consumo(self):
        """Consertar o PIX nao pode apagar o consumo de tabela."""
        self.lancado(self.camila, "12.00")

        resp = self.client.patch(self.url(), {"pix": "180.00"}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self.registrados(), {"Camila": Decimal("12.00")})

    def test_lista_vazia_apaga_o_consumo(self):
        """Diferente de nao falar: aqui a gerencia disse que nao teve nenhum."""
        self.lancado(self.camila, "12.00")

        resp = self.client.patch(self.url(), {"consumos": []}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Consumo.objects.count(), 0)

    def test_a_resposta_traz_o_consumo_no_formato_de_leitura(self):
        """O painel troca a linha pelo retorno do PATCH, sem refetch: sem o
        nome, o cartao mostraria o consumo sem dizer de quem e."""
        resp = self.client.patch(
            self.url(),
            {"consumos": [self.consumo(self.camila, "12.00")]},
            format="json",
        )

        self.assertEqual(
            [(c["nome"], str(c["valor"])) for c in resp.data["consumos"]],
            [("Camila", "12.00")],
        )
        self.assertEqual(
            str(resp.data["consumos"][0]["encarregado"]), str(self.camila.public_id)
        )

    def test_pessoa_de_outra_conta_e_recusada(self):
        """Estar logado nao limita o payload: o id da empresa vizinha cabe aqui
        do mesmo jeito que cabia no endpoint publico."""
        vizinha = Conta.objects.create(nome="Aurora Salgados")
        de_fora = Encarregado.objects.create(nome="Carlos", conta=vizinha)

        resp = self.client.patch(
            self.url(),
            {"consumos": [self.consumo(de_fora, "12.00")]},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(Consumo.objects.count(), 0)

    def test_quem_nao_consome_fica_fora_da_lista(self):
        sem_consumo = Encarregado.objects.create(
            nome="Estagiario", conta=self.conta, pode_consumir=False
        )

        resp = self.client.patch(
            self.url(),
            {"consumos": [self.consumo(sem_consumo, "12.00")]},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(Consumo.objects.count(), 0)

    def test_valor_zerado_e_recusado(self):
        self.lancado(self.camila, "12.00")

        resp = self.client.patch(
            self.url(),
            {"consumos": [self.consumo(self.camila, "0.00")]},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(self.registrados(), {"Camila": Decimal("12.00")})

    def test_consumo_recusado_nao_grava_o_resto_do_patch(self):
        """Um envio so, como no lancamento: o PIX nao pode entrar sozinho e
        deixar a gerencia achando que a correcao inteira passou."""
        resp = self.client.patch(
            self.url(),
            {
                "pix": "180.00",
                "consumos": [self.consumo(self.camila, "0.00")],
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.fechamento.refresh_from_db()
        self.assertEqual(self.fechamento.pix, Decimal("100.00"))
