from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from app.models import (
    Conta,
    Despesa,
    Encarregado,
    FechamentoCaixa,
    Feriado,
    ResponsavelRetirada,
)
from .fabricas import DIA_COMUM, criar_loja, lista, turno_de_hoje, vincular_conta


class FechamentoCaixaPublicoTests(APITestCase):
    """O lancamento e publico: o funcionario da loja nao tem login de usuario,
    mas o aparelho precisa ja ter entrado com o codigo da empresa."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = APIClient()
        self.conta = Conta.objects.create(nome="Marina")
        self.loja = criar_loja(
            nome_loja="Loja Centro", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.loja_inativa = criar_loja(
            nome_loja="Loja Fechada", cidade="Patos", endereco="Rua 2",
            conta=self.conta, ativo=False,
        )
        self.marina = ResponsavelRetirada.objects.create(
            nome="Marina", conta=self.conta
        )
        self.ana = Encarregado.objects.create(nome="Ana Paula", conta=self.conta)
        self.entrar()

    def entrar(self, conta=None):
        conta = conta or self.conta
        self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": conta.codigo_acesso, "apelido": "Caixa"},
            format="json",
        )

    def payload(self, **extra):
        base = {
            "loja": str(self.loja.public_id),
            "lancado_por": str(self.ana.public_id),
            "data": DIA_COMUM,
            "periodo": "TARDE",
            "pix": "2140.00",
            "cartao": "2272.50",
            "dinheiro": "400.00",
            "link_pagamento": "0.00",
            "houve_retirada": False,
            "houve_despesa": False,
            "houve_desperdicio": False,
        }
        base.update(extra)
        return base

    def test_funcionario_lanca_sem_login(self):
        resp = self.client.post("/api/v1/fechamentos-caixa/", self.payload(), format="json")

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(str(resp.data["total"]), "4812.50")
        self.assertEqual(FechamentoCaixa.objects.count(), 1)

    def test_retirada_e_despesa_voltam_para_o_total(self):
        """O numero que a gerencia le e quanto a loja movimentou.

        `dinheiro` e o que sobrou na gaveta: a retirada e a despesa ja sairam
        dali. Voltam somando porque o dinheiro saiu DEPOIS da venda — e a
        venda valeu. Ate 2026-09-01 esta conta subtraia as duas, descontando o
        turno duas vezes.
        """
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(
                houve_retirada=True,
                responsavel_retirada=str(self.marina.public_id),
                valor_retirado="300.00",
                despesas=[{"descricao": "Gas", "valor": "120.00"}],
            ),
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        # 4812.50 recebido + 300 de retirada + 120 de despesa.
        self.assertEqual(str(resp.data["total"]), "5232.50")
        self.assertEqual(str(resp.data["registrado"]), "420.00")

    def test_link_de_pagamento_entra_no_total(self):
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(link_pagamento="100.00"),
            format="json",
        )

        self.assertEqual(str(resp.data["total"]), "4912.50")

    def test_data_vem_do_servidor_quando_nao_enviada(self):
        # Sem data no payload — e por isso o turno precisa ser o de hoje: num
        # domingo "TARDE" nao existe, e o teste morreria por outro motivo.
        payload = self.payload(periodo=turno_de_hoje(self.conta))
        payload.pop("data")

        self.client.post("/api/v1/fechamentos-caixa/", payload, format="json")

        self.assertEqual(FechamentoCaixa.objects.get().data, timezone.localdate())

    def _ultimo_domingo(self):
        hoje = timezone.localdate()
        return hoje - timedelta(days=(hoje.weekday() + 1) % 7)

    def test_domingo_nao_tem_manha_nem_tarde(self):
        """A loja abre mais tarde e fecha mais tarde: o domingo nao e nenhum
        dos dois turnos do dia comum."""
        domingo = self._ultimo_domingo()

        for turno in ("MANHA", "TARDE"):
            with self.subTest(turno=turno):
                resp = self.client.post(
                    "/api/v1/fechamentos-caixa/",
                    self.payload(data=domingo.isoformat(), periodo=turno),
                    format="json",
                )

                self.assertEqual(resp.status_code, 400)
                self.assertIn("periodo", resp.data)

    def test_domingo_passa_no_turno_de_domingo(self):
        domingo = self._ultimo_domingo()

        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(data=domingo.isoformat(), periodo="DOMINGO"),
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.data)

    def test_domingo_nao_aceita_o_turno_do_dia_inteiro(self):
        """O turno do dia inteiro e do feriado. Aceitar os dois no domingo
        deixaria o mesmo dia gravado de duas formas, e o filtro do painel
        mostraria metade dos domingos."""
        domingo = self._ultimo_domingo()

        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(data=domingo.isoformat(), periodo="DIA"),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_data_no_futuro_e_recusada(self):
        amanha = (timezone.localdate() + timedelta(days=1)).isoformat()

        resp = self.client.post(
            "/api/v1/fechamentos-caixa/", self.payload(data=amanha), format="json"
        )

        self.assertEqual(resp.status_code, 400)

    def test_loja_inativa_nao_aceita_lancamento(self):
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(loja=str(self.loja_inativa.public_id)),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_formas_de_pagamento_em_branco_sao_validas(self):
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(pix="0.00", cartao="0.00", dinheiro="0.00", link_pagamento="0.00"),
            format="json",
        )

        self.assertEqual(resp.status_code, 201)

    def test_retirada_sim_exige_quem_retirou_e_valor(self):
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(houve_retirada=True),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("valor_retirado", resp.data)

    def test_retirada_sim_com_dados_completos_passa(self):
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(
                houve_retirada=True,
                responsavel_retirada=str(self.marina.public_id),
                valor_retirado="200.00",
            ),
            format="json",
        )

        self.assertEqual(resp.status_code, 201)

    def test_responsavel_de_outra_conta_e_recusado(self):
        """O public_id da loja circula em link publico — o payload e checado."""
        outra_conta = Conta.objects.create(nome="Aurora Salgados")
        intruso = ResponsavelRetirada.objects.create(nome="Fulano", conta=outra_conta)

        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(
                houve_retirada=True,
                responsavel_retirada=str(intruso.public_id),
                valor_retirado="200.00",
            ),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("responsavel_retirada", resp.data)

    def test_despesa_sim_exige_descricao_e_valor(self):
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(houve_despesa=True),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("despesa_valor", resp.data)

    def test_desperdicio_sim_exige_detalhes(self):
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(houve_desperdicio=True),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("desperdicio_detalhes", resp.data)

    def test_funcionario_nao_lista_nem_apaga(self):
        FechamentoCaixa.objects.create(
            loja=self.loja, nome_funcionario="Ana", data=timezone.localdate(),
            periodo="TARDE",
        )

        listagem = self.client.get("/api/v1/fechamentos-caixa/")

        self.assertIn(listagem.status_code, (401, 403))


class SeletoresDoFormularioTests(APITestCase):
    """Os dropdowns do formulario: precisam do aparelho logado; o ?conta= da
    URL nao decide mais nada (era o que vazava loja de outra empresa)."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = APIClient()
        self.conta = Conta.objects.create(nome="Marina")
        self.outra_conta = Conta.objects.create(nome="Aurora Salgados")

        criar_loja(
            nome_loja="Loja Centro", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        criar_loja(
            nome_loja="Loja Inativa", cidade="Patos", endereco="Rua 2",
            conta=self.conta, ativo=False,
        )
        criar_loja(
            nome_loja="Loja do Aurora", cidade="Patos", endereco="Rua 3",
            conta=self.outra_conta,
        )
        ResponsavelRetirada.objects.create(nome="Marina", conta=self.conta)
        ResponsavelRetirada.objects.create(nome="Aurora", conta=self.outra_conta)

    def entrar(self, conta=None):
        conta = conta or self.conta
        self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": conta.codigo_acesso, "apelido": "Caixa"},
            format="json",
        )

    def nomes(self, resp, campo):
        return [item[campo] for item in lista(resp)]

    def test_lojas_do_aparelho_logado(self):
        self.entrar()

        resp = self.client.get("/api/v1/lojas/")

        self.assertEqual(resp.status_code, 200)
        # Sem a loja inativa e sem a loja da outra conta.
        self.assertEqual(self.nomes(resp, "nome_loja"), ["Loja Centro"])

    def test_conta_da_url_nao_manda_mais_em_nada(self):
        """Antes o apelido da URL escolhia a conta; agora quem manda e o
        aparelho — mesmo pedindo a conta certa por engano na URL."""
        self.entrar()

        resp = self.client.get(f"/api/v1/lojas/?conta={self.outra_conta.slug}")

        self.assertEqual(self.nomes(resp, "nome_loja"), ["Loja Centro"])

    def test_apelido_nasce_do_nome_da_conta(self):
        self.assertEqual(self.conta.slug, "marina")
        self.assertEqual(Conta.objects.get(nome="Aurora Salgados").slug, "aurora-salgados")

    def test_lojas_sem_token_e_barrado(self):
        resp = self.client.get("/api/v1/lojas/")

        self.assertEqual(resp.status_code, 401)

    def test_lojas_do_anonimo_nao_expoem_cadastro(self):
        self.entrar()

        resp = self.client.get("/api/v1/lojas/")

        self.assertEqual(
            set(lista(resp)[0].keys()), {"id", "nome_loja"}
        )

    def test_responsaveis_do_aparelho_logado(self):
        self.entrar()

        resp = self.client.get("/api/v1/responsaveis-retirada/")

        self.assertEqual(self.nomes(resp, "nome"), ["Marina"])

    def test_responsaveis_sem_token_e_barrado(self):
        resp = self.client.get("/api/v1/responsaveis-retirada/")

        self.assertEqual(resp.status_code, 401)


class FechamentoCaixaPainelTests(APITestCase):
    """Leitura e correcao: so Admin/Gerente, e cada um so dentro da sua conta."""

    def setUp(self):
        for nome in ("Admin", "Gerente", "Responsavel"):
            Group.objects.get_or_create(name=nome)

        self.conta = Conta.objects.create(nome="Marina")
        self.outra_conta = Conta.objects.create(nome="Aurora Salgados")

        self.super_admin = User.objects.create_superuser(
            username="dono@plataforma.com", password="123456"
        )

        self.gerente = User.objects.create_user(username="ger@x.com", password="123456")
        self.gerente.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(self.gerente, self.conta)

        self.gerente_alheio = User.objects.create_user(
            username="ger@aurora.com", password="123456"
        )
        self.gerente_alheio.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(self.gerente_alheio, self.outra_conta)

        self.sem_conta = User.objects.create_user(username="solto@x.com", password="123456")
        self.sem_conta.groups.add(Group.objects.get(name="Gerente"))

        self.responsavel = User.objects.create_user(username="resp@x.com", password="123456")
        self.responsavel.groups.add(Group.objects.get(name="Responsavel"))
        vincular_conta(self.responsavel, self.conta)

        self.loja = criar_loja(
            nome_loja="Loja A", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.loja_alheia = criar_loja(
            nome_loja="Loja B", cidade="Patos", endereco="Rua 2", conta=self.outra_conta
        )

        self.hoje = timezone.localdate()
        self.fechamento = FechamentoCaixa.objects.create(
            loja=self.loja, nome_funcionario="Ana", data=self.hoje,
            periodo="TARDE", pix=100, cartao=0, dinheiro=0, link_pagamento=0,
        )
        FechamentoCaixa.objects.create(
            loja=self.loja_alheia, nome_funcionario="Carlos", data=self.hoje,
            periodo="TARDE", pix=50, cartao=0, dinheiro=0, link_pagamento=0,
        )

    def autenticado(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def linhas(self, resp):
        return lista(resp)

    def test_super_admin_ve_todas_as_contas(self):
        resp = self.autenticado(self.super_admin).get("/api/v1/fechamentos-caixa/")

        self.assertEqual(len(self.linhas(resp)), 2)

    def test_gerente_ve_so_a_propria_conta(self):
        resp = self.autenticado(self.gerente).get("/api/v1/fechamentos-caixa/")

        self.assertEqual([f["loja_nome"] for f in self.linhas(resp)], ["Loja A"])

    def test_gerente_da_outra_conta_ve_so_a_dele(self):
        resp = self.autenticado(self.gerente_alheio).get("/api/v1/fechamentos-caixa/")

        self.assertEqual([f["loja_nome"] for f in self.linhas(resp)], ["Loja B"])

    def test_usuario_sem_conta_nao_ve_nada(self):
        resp = self.autenticado(self.sem_conta).get("/api/v1/fechamentos-caixa/")

        self.assertEqual(self.linhas(resp), [])

    def test_gerente_nao_alcanca_fechamento_de_outra_conta(self):
        alheio = FechamentoCaixa.objects.get(loja=self.loja_alheia)

        resp = self.autenticado(self.gerente).patch(
            f"/api/v1/fechamentos-caixa/{alheio.public_id}/",
            {"pix": "999.00"}, format="json",
        )

        self.assertEqual(resp.status_code, 404)

    def test_responsavel_nao_ve_o_painel(self):
        resp = self.autenticado(self.responsavel).get("/api/v1/fechamentos-caixa/")

        self.assertEqual(resp.status_code, 403)

    def test_filtro_por_data_e_periodo(self):
        client = self.autenticado(self.super_admin)

        vazio = client.get(f"/api/v1/fechamentos-caixa/?data={self.hoje}&periodo=MANHA")
        cheio = client.get(f"/api/v1/fechamentos-caixa/?data={self.hoje}&periodo=TARDE")

        self.assertEqual(len(self.linhas(vazio)), 0)
        self.assertEqual(len(self.linhas(cheio)), 2)

    def test_filtro_por_intervalo_de_datas(self):
        """A tela de graficos pede semana, que nao cabe em ?data= nem ?mes=."""
        from datetime import timedelta

        antiga = self.hoje - timedelta(days=10)
        FechamentoCaixa.objects.create(
            loja=self.loja, nome_funcionario="Ana", data=antiga,
            periodo="MANHA", pix=1, cartao=0, dinheiro=0, link_pagamento=0,
        )
        client = self.autenticado(self.super_admin)

        semana = client.get(
            f"/api/v1/fechamentos-caixa/?de={self.hoje - timedelta(days=6)}&ate={self.hoje}"
        )
        tudo = client.get(f"/api/v1/fechamentos-caixa/?ate={self.hoje}")

        # O de 10 dias atras fica de fora da semana e entra no sem limite inferior.
        self.assertEqual(len(self.linhas(semana)), 2)
        self.assertEqual(len(self.linhas(tudo)), 3)

    def test_correcao_registra_quem_editou(self):
        resp = self.autenticado(self.gerente).patch(
            f"/api/v1/fechamentos-caixa/{self.fechamento.public_id}/",
            {"pix": "150.00"}, format="json",
        )

        self.fechamento.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(str(self.fechamento.pix), "150.00")
        self.assertEqual(self.fechamento.editado_por, self.gerente)

    def test_conferir_marca_o_turno_como_visto(self):
        resp = self.autenticado(self.gerente).patch(
            f"/api/v1/fechamentos-caixa/{self.fechamento.public_id}/conferir/"
        )

        self.fechamento.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.fechamento.conferido)
        self.assertEqual(self.fechamento.conferido_por, self.gerente)
        self.assertIsNotNone(self.fechamento.conferido_em)
        # Conferir nao e corrigir: nao carimba editado_por.
        self.assertIsNone(self.fechamento.editado_por)

    def test_conferir_fechamento_de_outra_conta_nao_alcanca(self):
        alheio = FechamentoCaixa.objects.get(loja=self.loja_alheia)

        resp = self.autenticado(self.gerente).patch(
            f"/api/v1/fechamentos-caixa/{alheio.public_id}/conferir/"
        )

        self.assertEqual(resp.status_code, 404)

    def test_pendentes_lista_quem_nao_lancou(self):
        criar_loja(nome_loja="Loja C", cidade="Patos", endereco="Rua 3", conta=self.conta)

        resp = self.autenticado(self.gerente).get(
            f"/api/v1/fechamentos-caixa/pendentes/?data={self.hoje}&periodo=TARDE"
        )

        self.assertEqual([l["nome_loja"] for l in resp.data], ["Loja C"])

    def test_pendentes_exige_data(self):
        resp = self.autenticado(self.gerente).get("/api/v1/fechamentos-caixa/pendentes/")

        self.assertEqual(resp.status_code, 400)


class ContaEndpointTests(APITestCase):
    """/api/v1/contas/ e do dono da plataforma, de mais ninguem."""

    def setUp(self):
        Group.objects.get_or_create(name="Gerente")
        self.conta = Conta.objects.create(nome="Marina")
        Conta.objects.create(nome="Aurora Salgados")

        self.super_admin = User.objects.create_superuser(
            username="dono@plataforma.com", password="123456"
        )
        self.gerente = User.objects.create_user(username="ger@x.com", password="123456")
        self.gerente.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(self.gerente, self.conta)

    def test_super_admin_lista_as_contas(self):
        client = APIClient()
        client.force_authenticate(self.super_admin)

        resp = client.get("/api/v1/contas/")

        nomes = {c["nome"] for c in lista(resp)}
        self.assertEqual(nomes, {"Marina", "Aurora Salgados"})

    def test_gerente_nao_lista_conta_nenhuma(self):
        client = APIClient()
        client.force_authenticate(self.gerente)

        resp = client.get("/api/v1/contas/")

        self.assertEqual(lista(resp), [])

    def test_anonimo_nao_acessa(self):
        resp = APIClient().get("/api/v1/contas/")

        self.assertIn(resp.status_code, (401, 403))


class TurnoUnicoTests(APITestCase):
    """Domingo e feriado a loja faz um lancamento so, do expediente inteiro.

    Ja foi gravado como "manha", e o painel mostrava "Manha" para um numero do
    dia todo — a loja abre mais tarde e fecha mais tarde nesses dias.
    """

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = APIClient()
        self.conta = Conta.objects.create(nome="Marina")
        self.loja = criar_loja(
            nome_loja="Loja Centro", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.ana = Encarregado.objects.create(nome="Ana Paula", conta=self.conta)
        self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": self.conta.codigo_acesso, "apelido": "Caixa"},
            format="json",
        )

    def payload(self, **extra):
        base = {
            "loja": str(self.loja.public_id),
            "lancado_por": str(self.ana.public_id),
            "data": DIA_COMUM,
            "periodo": "TARDE",
            "pix": "100.00",
            "cartao": "0.00",
            "dinheiro": "0.00",
            "link_pagamento": "0.00",
            "houve_retirada": False,
            "houve_despesa": False,
            "houve_desperdicio": False,
        }
        base.update(extra)
        return base

    def test_feriado_nacional_recusa_a_tarde(self):
        # Natal de 2025 caiu numa quinta — nao da para confundir com a regra
        # do domingo. E ja passou: data futura tem validacao propria.
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(data="2025-12-25"),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Natal", str(resp.data["periodo"]))

    def test_feriado_movel_tambem_conta(self):
        """Carnaval, Sexta-feira Santa e Corpus Christi mudam de data todo ano."""
        from app.feriados import feriados_nacionais

        moveis = {
            nome: dia
            for dia, nome in feriados_nacionais(2025).items()
            if nome in ("Carnaval", "Sexta-feira Santa", "Corpus Christi")
        }
        self.assertEqual(len(moveis), 3)

        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(data=moveis["Carnaval"].isoformat()),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_feriado_local_da_conta_recusa_a_tarde(self):
        Feriado.objects.create(
            conta=self.conta, data="2026-08-05", descricao="Padroeira da cidade"
        )

        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(data="2026-08-05"),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Padroeira", str(resp.data["periodo"]))

    def test_feriado_local_de_outra_conta_nao_afeta(self):
        outra = Conta.objects.create(nome="Aurora Salgados")
        Feriado.objects.create(
            conta=outra, data="2026-08-05", descricao="Feriado do Aurora"
        )

        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(data="2026-08-05"),
            format="json",
        )

        self.assertEqual(resp.status_code, 201)

    def test_dia_comum_aceita_a_tarde(self):
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(data="2026-08-05"),
            format="json",
        )

        self.assertEqual(resp.status_code, 201)

    def test_feriado_passa_como_turno_do_dia(self):
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(data="2025-12-25", periodo="DIA"),
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.data)

    def test_feriado_nao_e_domingo(self):
        """25/12/2025 caiu numa quinta. Gravar como "DOMINGO" poria o Natal no
        meio dos domingos que a gerencia compara um com o outro."""
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(data="2025-12-25", periodo="DOMINGO"),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_feriado_tambem_recusa_a_manha(self):
        """O turno unico do feriado nao e a manha: recusar so a tarde deixaria
        passar justamente o lancamento que esta migracao veio corrigir."""
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            self.payload(data="2025-12-25", periodo="MANHA"),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Natal", str(resp.data["periodo"]))

    def test_dia_comum_recusa_os_turnos_de_dia_inteiro(self):
        """Numa quarta-feira a loja tem manha e tarde: aceitar "DIA" ou
        "DOMINGO" ali seria deixar dois expedientes virarem uma linha so no
        painel — e um domingo numa quarta."""
        for turno in ("DIA", "DOMINGO"):
            with self.subTest(turno=turno):
                resp = self.client.post(
                    "/api/v1/fechamentos-caixa/",
                    self.payload(data="2026-08-05", periodo=turno),
                    format="json",
                )

                self.assertEqual(resp.status_code, 400)
                self.assertIn("manha", str(resp.data["periodo"]))

    def test_endpoint_de_turnos_exige_o_aparelho_e_explica_o_motivo(self):
        """O endpoint deixou de ser publico: precisa do aparelho ja logado.
        O ?conta= nao serve mais — a conta vem do token."""
        resp = self.client.get("/api/v1/fechamentos-caixa/turnos/?data=2025-12-25")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["periodos"], ["DIA"])
        self.assertIn("Natal", resp.data["motivo"])

    def test_endpoint_de_turnos_no_domingo_oferece_domingo(self):
        # 30/08/2026 e um domingo.
        resp = self.client.get("/api/v1/fechamentos-caixa/turnos/?data=2026-08-30")

        self.assertEqual(resp.data["periodos"], ["DOMINGO"])
        self.assertEqual(resp.data["motivo"], "Domingo")

    def test_endpoint_de_turnos_em_dia_comum(self):
        resp = self.client.get("/api/v1/fechamentos-caixa/turnos/?data=2026-08-05")

        self.assertEqual(resp.data["periodos"], ["MANHA", "TARDE"])
        self.assertIsNone(resp.data["motivo"])


class JanelaDeCorrecaoDaLojaTests(APITestCase):
    """Os 20 minutos em que quem lancou ainda conserta sozinho.

    A janela existe porque erro de digitacao aparece logo depois de enviar,
    com o funcionario ainda na loja — e nesse instante chamar a gerencia para
    trocar um digito custa mais do que o erro.
    """

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = APIClient()
        self.conta = Conta.objects.create(nome="Marina")
        self.loja = criar_loja(
            nome_loja="Loja Centro", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.marina = ResponsavelRetirada.objects.create(
            nome="Marina", conta=self.conta
        )
        self.ana = Encarregado.objects.create(nome="Ana Paula", conta=self.conta)
        self.fechamento = FechamentoCaixa.objects.create(
            loja=self.loja,
            nome_funcionario="Ana Paula",
            data=timezone.localdate(),
            periodo="TARDE",
            pix="100.00", cartao="0.00", dinheiro="50.00", link_pagamento="0.00",
        )
        self.url = f"/api/v1/fechamentos-caixa/{self.fechamento.public_id}/correcao/"
        self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": self.conta.codigo_acesso, "apelido": "Caixa"},
            format="json",
        )

    def envelhecer(self, minutos):
        """created_at e auto_now_add: so da para envelhecer por UPDATE."""
        FechamentoCaixa.objects.filter(pk=self.fechamento.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutos)
        )
        self.fechamento.refresh_from_db()

    def payload(self, **extra):
        base = {
            "loja": str(self.loja.public_id),
            "lancado_por": str(self.ana.public_id),
            "data": DIA_COMUM,
            "periodo": "TARDE",
            "pix": "100.00",
            "cartao": "0.00",
            "dinheiro": "500.00",
            "link_pagamento": "0.00",
            "houve_retirada": False,
            "houve_despesa": False,
            "houve_desperdicio": False,
        }
        base.update(extra)
        return base

    def test_confirmacao_do_post_diz_quanto_tempo_sobra(self):
        """O prazo vem do servidor: o relogio do celular da loja nao serve."""
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/", self.payload(), format="json"
        )

        self.assertEqual(resp.status_code, 201)
        self.assertGreater(resp.data["segundos_para_corrigir"], 20 * 60 - 30)
        self.assertLessEqual(resp.data["segundos_para_corrigir"], 20 * 60)

    def test_loja_reabre_o_formulario_dentro_da_janela(self):
        """Fechar a aba e voltar nao pode custar a correcao."""
        resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["nome_funcionario"], "Ana Paula")
        self.assertEqual(str(resp.data["dinheiro"]), "50.00")
        self.assertEqual(resp.data["loja"], self.loja.public_id)

    def test_loja_corrige_sem_login_dentro_da_janela(self):
        self.envelhecer(19)

        resp = self.client.patch(self.url, self.payload(), format="json")

        self.assertEqual(resp.status_code, 200)
        self.fechamento.refresh_from_db()
        self.assertEqual(str(self.fechamento.dinheiro), "500.00")
        self.assertEqual(str(resp.data["total"]), "600.00")

    def test_correcao_da_loja_nao_conta_como_edicao_da_gerencia(self):
        """editado_por e a prova de que o numero nao e mais o que a loja
        mandou — dentro da janela ainda e a loja mandando."""
        self.client.patch(self.url, self.payload(), format="json")

        self.fechamento.refresh_from_db()
        self.assertIsNone(self.fechamento.editado_por)

    def test_depois_de_20_minutos_nao_da_mais(self):
        self.envelhecer(21)

        resp = self.client.patch(self.url, self.payload(), format="json")

        self.assertEqual(resp.status_code, 403)
        self.fechamento.refresh_from_db()
        self.assertEqual(str(self.fechamento.dinheiro), "50.00")

    def test_depois_de_20_minutos_nem_abre_o_formulario(self):
        self.envelhecer(21)

        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_corrigir_nao_renova_o_prazo(self):
        """Senao bastava salvar de novo a cada 19 minutos para editar sempre."""
        self.envelhecer(19)
        self.client.patch(self.url, self.payload(), format="json")

        self.fechamento.refresh_from_db()
        self.assertLess(self.fechamento.segundos_para_corrigir, 90)

    def test_turno_ja_conferido_fecha_a_janela_na_hora(self):
        """Deixar corrigir por baixo desfaria a conferencia sem ninguem ver."""
        self.fechamento.conferido = True
        self.fechamento.save(update_fields=["conferido"])

        resp = self.client.patch(self.url, self.payload(), format="json")

        self.assertEqual(resp.status_code, 403)

    def test_correcao_passa_pelas_mesmas_validacoes(self):
        """A janela relaxa o prazo, nao as regras do formulario."""
        resp = self.client.patch(
            self.url,
            self.payload(houve_despesa=True, despesa_descricao="", despesa_valor=None),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_id_inexistente_da_404(self):
        resp = self.client.get(
            "/api/v1/fechamentos-caixa/00000000-0000-4000-8000-000000000000/correcao/"
        )

        self.assertEqual(resp.status_code, 404)


class CorrecaoPelaGerenciaTests(APITestCase):
    """O PATCH da tela por loja: corrigir um turno ja lancado."""

    def setUp(self):
        self.client = APIClient()
        self.conta = Conta.objects.create(nome="Marina")
        self.loja = criar_loja(
            nome_loja="Loja Centro", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.responsavel = ResponsavelRetirada.objects.create(
            nome="Marina", conta=self.conta
        )
        self.gerente = User.objects.create_user(
            username="gerente", email="gerente@x.com", password="x"
        )
        self.gerente.groups.add(Group.objects.get_or_create(name="Gerente")[0])
        vincular_conta(self.gerente, self.conta)
        self.client.force_authenticate(self.gerente)

        self.fechamento = FechamentoCaixa.objects.create(
            loja=self.loja,
            nome_funcionario="Ana Paula",
            data=timezone.localdate(),
            periodo="TARDE",
            pix="100.00", cartao="0.00", dinheiro="50.00", link_pagamento="0.00",
        )
        self.url = f"/api/v1/fechamentos-caixa/{self.fechamento.public_id}/"

    def test_gerente_corrige_um_valor(self):
        resp = self.client.patch(self.url, {"dinheiro": "500.00"}, format="json")

        self.assertEqual(resp.status_code, 200)
        self.fechamento.refresh_from_db()
        self.assertEqual(str(self.fechamento.dinheiro), "500.00")
        self.assertEqual(str(resp.data["total"]), "600.00")

    def test_correcao_da_gerencia_fica_registrada(self):
        """editado_por e a prova de que o numero nao e mais o que a loja mandou."""
        self.client.patch(self.url, {"dinheiro": "500.00"}, format="json")

        self.fechamento.refresh_from_db()
        self.assertEqual(self.fechamento.editado_por, self.gerente)

    def test_nao_da_para_esvaziar_metade_de_uma_despesa(self):
        Despesa.objects.create(
            fechamento=self.fechamento, descricao="Gas", valor="120.00"
        )

        resp = self.client.patch(
            self.url, {"despesas": [{"descricao": "Gas"}]}, format="json"
        )

        self.assertEqual(resp.status_code, 400)

    def test_a_gerencia_corrige_o_valor_do_gas(self):
        """A correcao mais comum depois que a loja manda."""
        Despesa.objects.create(
            fechamento=self.fechamento, descricao="Gas", valor="120.00"
        )

        resp = self.client.patch(
            self.url,
            {"despesas": [{"descricao": "Gas", "valor": "95.00"}]},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self.fechamento.despesas.count(), 1)
        self.assertEqual(str(self.fechamento.despesas.get().valor), "95.00")
        # A resposta volta no formato da listagem, com id — o painel troca a
        # linha pelo retorno, sem refetch.
        self.assertIn("id", resp.data["despesas"][0])

    def test_apagar_as_despesas_passa(self):
        """Lista vazia e "nao teve nenhuma", e apaga o que estava la."""
        Despesa.objects.create(
            fechamento=self.fechamento, descricao="Gas", valor="120.00"
        )

        resp = self.client.patch(self.url, {"despesas": []}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self.fechamento.despesas.count(), 0)

    def test_correcao_sem_falar_de_despesa_preserva_as_que_existem(self):
        """Sem esta diferenca, corrigir o PIX apagaria a despesa de tabela."""
        Despesa.objects.create(
            fechamento=self.fechamento, descricao="Gas", valor="120.00"
        )

        resp = self.client.patch(self.url, {"pix": "10.00"}, format="json")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self.fechamento.despesas.count(), 1)

    def test_retirada_precisa_de_quem_retirou(self):
        resp = self.client.patch(
            self.url, {"houve_retirada": True, "valor_retirado": "300.00"}, format="json"
        )

        self.assertEqual(resp.status_code, 400)

    def test_gerente_liga_a_retirada_completa(self):
        resp = self.client.patch(
            self.url,
            {
                "houve_retirada": True,
                "responsavel_retirada": str(self.responsavel.public_id),
                "valor_retirado": "300.00",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        # A retirada SOMA: o dinheiro contado ja estava sem ela.
        self.assertEqual(str(resp.data["registrado"]), "300.00")
        self.assertEqual(resp.data["responsavel_retirada_nome"], "Marina")

    def test_desperdicio_sem_texto_nao_passa(self):
        resp = self.client.patch(self.url, {"houve_desperdicio": True}, format="json")

        self.assertEqual(resp.status_code, 400)

    def test_turno_de_outra_conta_nao_existe_para_este_gerente(self):
        outra = Conta.objects.create(nome="Aurora Salgados")
        alheio = FechamentoCaixa.objects.create(
            loja=criar_loja(
                nome_loja="Outra", cidade="Patos", endereco="Rua 9", conta=outra
            ),
            nome_funcionario="Fulano",
            data=timezone.localdate(),
            periodo="MANHA",
            pix="10.00",
        )

        resp = self.client.patch(
            f"/api/v1/fechamentos-caixa/{alheio.public_id}/",
            {"pix": "9999.00"},
            format="json",
        )

        self.assertEqual(resp.status_code, 404)
        alheio.refresh_from_db()
        self.assertEqual(str(alheio.pix), "10.00")
