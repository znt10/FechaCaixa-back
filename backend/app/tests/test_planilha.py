"""A exportacao do periodo para planilha.

A dona fecha o mes no Excel: o painel responde na tela, mas o desconto em folha
e a conferencia com o contador acontecem na planilha. O que estes testes
protegem e a separacao das abas — o consumo nao pode cair no meio das saidas do
caixa, porque somar a coluna inteira e a primeira coisa que se faz num arquivo
desses.
"""

from io import BytesIO

from django.contrib.auth.models import Group, User
from openpyxl import load_workbook
from rest_framework.test import APIClient, APITestCase

from app.models import (
    Conta,
    Consumo,
    Despesa,
    Encarregado,
    FechamentoCaixa,
    ResponsavelRetirada,
)

from .fabricas import DIA_COMUM, criar_loja, vincular_conta

URL = "/api/v1/planilha/"


class PlanilhaDoPeriodoTests(APITestCase):
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
            nome_loja="Loja Centro", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.marina = ResponsavelRetirada.objects.create(
            nome="Marina", conta=self.conta
        )
        self.camila = Encarregado.objects.create(nome="Camila", conta=self.conta)
        self.bruno = Encarregado.objects.create(nome="Bruno", conta=self.conta)

        self.fechamento = FechamentoCaixa.objects.create(
            loja=self.loja, nome_funcionario="Ana", data=DIA_COMUM, periodo="TARDE",
            pix=100, cartao=50, dinheiro=30, link_pagamento=0,
            houve_retirada=True, responsavel_retirada=self.marina, valor_retirado=10,
        )
        Despesa.objects.create(
            fechamento=self.fechamento, descricao="Gás", valor=20
        )
        Consumo.objects.create(
            fechamento=self.fechamento, encarregado=self.camila, valor="12.00"
        )
        Consumo.objects.create(
            fechamento=self.fechamento, encarregado=self.bruno, valor="8.00"
        )

    def baixar(self, user=None, **params):
        client = APIClient()
        client.force_authenticate(user or self.gerente)
        consulta = "&".join(f"{k}={v}" for k, v in params.items())
        resp = client.get(f"{URL}?{consulta}" if consulta else URL)
        self.assertEqual(resp.status_code, 200, resp.content[:200])
        return load_workbook(BytesIO(resp.content))

    def linhas(self, folha):
        return list(folha.iter_rows(min_row=2, values_only=True))

    def test_as_cinco_abas_existem_e_na_ordem_de_leitura(self):
        planilha = self.baixar(de=DIA_COMUM, ate=DIA_COMUM)

        self.assertEqual(
            planilha.sheetnames,
            ["Entradas", "Saídas", "Consumo por pessoa", "Consumo", "Desperdício"],
        )

    def test_as_entradas_trazem_um_turno_por_linha(self):
        planilha = self.baixar(de=DIA_COMUM, ate=DIA_COMUM)

        linha = self.linhas(planilha["Entradas"])[0]
        self.assertEqual(linha[1], "Loja Centro")
        self.assertEqual(linha[2], "Tarde")
        # As seis parcelas, e o Total logo depois delas: quem arrastar a soma
        # pela linha tem que chegar no mesmo numero da coluna Total.
        self.assertEqual(linha[8], 10.0)   # retirada
        self.assertEqual(linha[9], 20.0)   # despesa
        self.assertEqual(linha[10], 210.0)  # total: 180 recebido + 10 + 20
        self.assertEqual(sum(linha[4:10]), linha[10])
        self.assertEqual(linha[11], 0)     # devolucao: nao houve

    def test_as_entradas_fecham_um_subtotal_por_loja(self):
        """Ela confere loja por loja. Sem o subtotal, o total de cada uma sai
        de arrastar o mouse pela coluna — e e ai que se soma o bloco errado."""
        outra_loja = criar_loja(
            nome_loja="Loja Sul", cidade="Patos", endereco="Rua 9", conta=self.conta
        )
        FechamentoCaixa.objects.create(
            loja=outra_loja, nome_funcionario="Ana", data=DIA_COMUM, periodo="MANHA",
            pix=10, cartao=0, dinheiro=0, link_pagamento=0,
        )

        planilha = self.baixar(de=DIA_COMUM, ate=DIA_COMUM)

        linhas = self.linhas(planilha["Entradas"])
        # Coluna 4 e "Quem lancou" nas linhas de dado e o rotulo nas de fecho.
        self.assertEqual(
            [linha[3] for linha in linhas],
            ["Ana", "Total Loja Centro", "Ana", "Total Loja Sul", "TOTAL"],
        )
        # Coluna 11 e o total do turno — ela veio para depois das parcelas.
        self.assertEqual(
            [linha[10] for linha in linhas], [210.0, 210.0, 10.0, 10.0, 220.0]
        )

    def test_o_consumo_nao_entra_nas_saidas(self):
        """A regra que a separacao existe para proteger: ninguem pagou na hora,
        entao o consumo nao saiu da gaveta. Somar a aba Saidas tem que dar o
        mesmo que a soma de retirada e despesa da aba Entradas."""
        planilha = self.baixar(de=DIA_COMUM, ate=DIA_COMUM)

        saidas = self.linhas(planilha["Saídas"])
        lancamentos = [linha for linha in saidas if linha[3]]
        self.assertEqual([linha[3] for linha in lancamentos], ["Despesa", "Retirada"])
        # O fecho diz a invariante em uma celula: tem que bater com retirada +
        # despesa da aba Entradas, e nao com o consumo somado junto.
        self.assertEqual(saidas[-1][4], "TOTAL")
        self.assertEqual(saidas[-1][5], 30.0)

    def test_as_saidas_fecham_um_subtotal_por_loja(self):
        outra_loja = criar_loja(
            nome_loja="Loja Sul", cidade="Patos", endereco="Rua 9", conta=self.conta
        )
        outro = FechamentoCaixa.objects.create(
            loja=outra_loja, nome_funcionario="Ana", data=DIA_COMUM, periodo="MANHA",
            pix=10, cartao=0, dinheiro=0, link_pagamento=0,
        )
        Despesa.objects.create(fechamento=outro, descricao="Água", valor=5)

        planilha = self.baixar(de=DIA_COMUM, ate=DIA_COMUM)

        linhas = self.linhas(planilha["Saídas"])
        # Coluna 5 e "Descricao" nas linhas de dado e o rotulo nas de fecho.
        self.assertEqual(
            [linha[4] for linha in linhas],
            ["Gás", "Marina", "Total Loja Centro", "Água", "Total Loja Sul", "TOTAL"],
        )
        self.assertEqual(
            [linha[5] for linha in linhas], [20.0, 10.0, 30.0, 5.0, 5.0, 35.0]
        )

    def test_varias_despesas_viram_varias_linhas(self):
        """O turno que comprou gas, agua e remedio sai com as tres separadas.

        Antes cabia uma so, e as outras iam empilhadas no campo de texto —
        onde a contabilidade nao consegue lancar cada uma.
        """
        Despesa.objects.create(
            fechamento=self.fechamento, descricao="Água", valor=15
        )
        Despesa.objects.create(
            fechamento=self.fechamento, descricao="Remédio", valor=5
        )

        planilha = self.baixar(de=DIA_COMUM, ate=DIA_COMUM)

        saidas = self.linhas(planilha["Saídas"])
        despesas = [linha[4] for linha in saidas if linha[3] == "Despesa"]
        self.assertEqual(despesas, ["Gás", "Remédio", "Água"])
        # E a coluna da aba Entradas traz a soma das tres.
        self.assertEqual(self.linhas(planilha["Entradas"])[0][9], 40.0)

    def test_a_devolucao_aparece_mas_nao_entra_no_total(self):
        """Ela saiu da gaveta, entao esta na aba Saidas. Mas cancelou a venda
        junto, entao nao mexe no Total da aba Entradas."""
        self.fechamento.houve_devolucao = True
        self.fechamento.devolucao_valor = 50
        self.fechamento.save()

        planilha = self.baixar(de=DIA_COMUM, ate=DIA_COMUM)

        entrada = self.linhas(planilha["Entradas"])[0]
        self.assertEqual(entrada[11], 50.0)   # coluna propria, depois do Total
        self.assertEqual(entrada[10], 210.0)  # o Total nao mudou

        saidas = self.linhas(planilha["Saídas"])
        self.assertIn("Devolução", [linha[3] for linha in saidas])

    def test_a_aba_do_desconto_soma_por_pessoa(self):
        # Segundo turno, mesma pessoa em outra loja: e o caso que a planilha
        # a mao nao fechava.
        outra_loja = criar_loja(
            nome_loja="Loja Sul", cidade="Patos", endereco="Rua 9", conta=self.conta
        )
        outro = FechamentoCaixa.objects.create(
            loja=outra_loja, nome_funcionario="Ana", data=DIA_COMUM, periodo="MANHA",
            pix=10, cartao=0, dinheiro=0, link_pagamento=0,
        )
        Consumo.objects.create(fechamento=outro, encarregado=self.camila, valor="5.00")

        planilha = self.baixar(de=DIA_COMUM, ate=DIA_COMUM)

        linhas = self.linhas(planilha["Consumo por pessoa"])
        camila = next(linha for linha in linhas if linha[0] == "Camila")
        self.assertEqual(camila[1], 17.0)
        self.assertEqual(camila[2], 2)
        self.assertEqual(camila[3], "Loja Centro, Loja Sul")

    def test_o_consumo_por_pessoa_vem_de_a_a_z(self):
        """A aba do desconto e uma lista de procura: ela abre para achar UMA
        pessoa e ver quanto descontar. Por nome se acha; por valor se procura a
        pessoa inteira ate encontrar."""
        planilha = self.baixar(de=DIA_COMUM, ate=DIA_COMUM)

        linhas = self.linhas(planilha["Consumo por pessoa"])
        # Bruno gastou 8 e Camila 12: por valor, Camila viria primeiro.
        self.assertEqual([linha[0] for linha in linhas], ["Bruno", "Camila"])

    def test_o_detalhe_do_consumo_diz_dia_loja_e_turno(self):
        planilha = self.baixar(de=DIA_COMUM, ate=DIA_COMUM)

        linhas = self.linhas(planilha["Consumo"])
        self.assertEqual(len(linhas), 2)
        self.assertEqual([linha[3] for linha in linhas], ["Bruno", "Camila"])
        self.assertEqual(linhas[0][1], "Loja Centro")
        self.assertEqual(linhas[0][2], "Tarde")

    def test_valor_sai_como_numero_e_data_como_data(self):
        """Texto formatado nao soma nem ordena — que e tudo o que ela vai fazer
        assim que o arquivo abrir."""
        from datetime import date

        planilha = self.baixar(de=DIA_COMUM, ate=DIA_COMUM)
        caixa = planilha["Entradas"]

        # (int, float) porque o Excel guarda 180,00 como 180 — o que importa
        # e nao ser texto, que e o que impediria a soma.
        self.assertIsInstance(caixa.cell(row=2, column=9).value, (int, float))
        self.assertEqual(caixa.cell(row=2, column=1).value.date(), date.fromisoformat(DIA_COMUM))
        self.assertEqual(caixa.cell(row=2, column=9).number_format, "#,##0.00")

    def test_fora_do_periodo_nao_entra(self):
        planilha = self.baixar(de="2026-08-06", ate="2026-08-06")

        self.assertEqual(self.linhas(planilha["Entradas"]), [])
        self.assertEqual(self.linhas(planilha["Consumo"]), [])

    def test_gerente_de_outra_conta_leva_a_planilha_vazia(self):
        planilha = self.baixar(self.gerente_alheio, de=DIA_COMUM, ate=DIA_COMUM)

        self.assertEqual(self.linhas(planilha["Entradas"]), [])
        self.assertEqual(self.linhas(planilha["Consumo por pessoa"]), [])

    def test_sem_login_nao_baixa(self):
        resp = APIClient().get(f"{URL}?de={DIA_COMUM}&ate={DIA_COMUM}")

        self.assertIn(resp.status_code, (401, 403))

    def test_o_arquivo_vem_nomeado_pela_empresa_e_pelo_periodo(self):
        client = APIClient()
        client.force_authenticate(self.gerente)

        resp = client.get(f"{URL}?de=2026-08-01&ate=2026-08-31")

        self.assertIn("attachment;", resp["Content-Disposition"])
        self.assertIn("2026-08-01-a-2026-08-31.xlsx", resp["Content-Disposition"])
        self.assertIn(self.conta.slug, resp["Content-Disposition"])

    def test_data_ilegivel_nao_derruba_a_exportacao(self):
        """O parametro vem da URL, e um 500 aqui seria a tela dizendo que o
        painel quebrou."""
        planilha = self.baixar(de="ontem", ate="")

        self.assertEqual(planilha.sheetnames[0], "Entradas")
