from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from app.models import (
    Conta,
    DispositivoDoFormulario,
    Encarregado,
    FechamentoCaixa,
)

from .fabricas import criar_loja


class ContaCodigoDeAcessoTests(TestCase):
    """O codigo e o que a loja digita: precisa ser curto, unico e legivel."""

    def test_conta_nova_ganha_codigo(self):
        conta = Conta.objects.create(nome="Aurora Salgados")

        self.assertRegex(conta.codigo_acesso, r"^[A-Z]{4}-\d{4}$")

    def test_codigo_nao_usa_caracteres_ambiguos(self):
        """Digitado a mao, O/0 e I/1 viram chamado de suporte."""
        conta = Conta.objects.create(nome="Oliveira Iogurtes")

        letras = conta.codigo_acesso.split("-")[0]
        self.assertNotIn("O", letras)
        self.assertNotIn("I", letras)

    def test_duas_contas_nao_repetem_codigo(self):
        primeira = Conta.objects.create(nome="Padaria Central")
        segunda = Conta.objects.create(nome="Padaria Central")

        self.assertNotEqual(primeira.codigo_acesso, segunda.codigo_acesso)

    def test_empresa_nasce_com_dois_fechamentos_por_dia(self):
        """O padrao e o que ja existe hoje: manha e tarde."""
        conta = Conta.objects.create(nome="Marina")

        self.assertEqual(conta.fechamentos_por_dia, 2)


class AcessoAoFormularioTests(APITestCase):
    URL = "/api/v1/formulario/acesso/"

    def setUp(self):
        from django.core.cache import cache

        # O throttle e 5/min; sem isto, o historico vaza de um teste para o
        # outro e o sexto POST da classe toma 429 em vez do status esperado.
        cache.clear()
        self.conta = Conta.objects.create(nome="Aurora Salgados")

    def test_codigo_certo_cria_dispositivo_e_devolve_cookie(self):
        resp = self.client.post(
            self.URL,
            {"codigo": self.conta.codigo_acesso, "apelido": "Celular do balcao"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["empresa"]["nome"], "Aurora Salgados")
        self.assertEqual(resp.data["empresa"]["fechamentos_por_dia"], 2)
        # O slug volta junto porque e para /<slug> que o aparelho e mandado
        # depois de entrar.
        self.assertEqual(resp.data["empresa"]["slug"], self.conta.slug)
        self.assertIn("formulario_token", resp.cookies)
        self.assertEqual(DispositivoDoFormulario.objects.count(), 1)

    def test_banco_nao_guarda_o_token_cru(self):
        resp = self.client.post(
            self.URL,
            {"codigo": self.conta.codigo_acesso, "apelido": "Tablet"},
            format="json",
        )

        token = resp.cookies["formulario_token"].value
        dispositivo = DispositivoDoFormulario.objects.get()
        self.assertNotEqual(dispositivo.token_hash, token)
        self.assertEqual(len(dispositivo.token_hash), 64)

    def test_codigo_sem_hifen_entra(self):
        """Ninguem digita o hifen. Recusar por causa dele e a tela brigando
        com quem esta tentando fechar o caixa."""
        resp = self.client.post(
            self.URL,
            {"codigo": self.conta.codigo_acesso.replace("-", "")},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)

    def test_codigo_com_espaco_no_lugar_do_hifen_entra(self):
        resp = self.client.post(
            self.URL,
            {"codigo": self.conta.codigo_acesso.replace("-", " ")},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)

    def test_codigo_minusculo_e_com_espaco_entra(self):
        """Teclado de celular capitaliza e cola espaco; isso nao e erro."""
        resp = self.client.post(
            self.URL,
            {"codigo": f"  {self.conta.codigo_acesso.lower()} ", "apelido": "Celular"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)

    def test_codigo_errado_nao_diz_se_a_empresa_existe(self):
        resp = self.client.post(
            self.URL, {"codigo": "AURO-0000", "apelido": "Celular"}, format="json"
        )

        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.data["detail"], "Codigo invalido.")
        self.assertEqual(DispositivoDoFormulario.objects.count(), 0)

    def test_conta_inativa_nao_entra(self):
        self.conta.ativo = False
        self.conta.save(update_fields=["ativo"])

        resp = self.client.post(
            self.URL,
            {"codigo": self.conta.codigo_acesso, "apelido": "Celular"},
            format="json",
        )

        self.assertEqual(resp.status_code, 401)

    def test_sem_apelido_o_aparelho_e_batizado_sozinho(self):
        """A loja tem um celular so: pedir um apelido no fim do expediente e
        uma pergunta a mais. Mas a lista de "desconectar" do painel junta os
        aparelhos de todas as lojas da empresa, entao ficar sem nome nenhum
        transformaria a escolha de qual derrubar em sorteio."""
        resp = self.client.post(
            self.URL,
            {"codigo": self.conta.codigo_acesso},
            format="json",
            HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("iPhone", DispositivoDoFormulario.objects.get().apelido)

    def test_apelido_digitado_ganha_do_automatico(self):
        resp = self.client.post(
            self.URL,
            {"codigo": self.conta.codigo_acesso, "apelido": "Celular do balcao"},
            format="json",
            HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            DispositivoDoFormulario.objects.get().apelido, "Celular do balcao"
        )


class RateLimitDoAcessoTests(APITestCase):
    """O teto de tentativas tem que valer tambem para quem esta logado.

    CookieJWTAuthentication autentica qualquer requisicao com access_token
    valido, independente da permission_class da view ser AllowAny. Se o
    throttle herdasse de AnonRateThrottle, o get_cache_key dele devolveria
    None para requisicao autenticada — ou seja, qualquer conta logada no
    mesmo navegador (loja, gerente, admin) testaria o codigo de qualquer
    outra empresa em velocidade total, sem teto nenhum.
    """

    URL = "/api/v1/formulario/acesso/"

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        self.usuario = User.objects.create_user(
            username="gerente@fechacaixa.com",
            email="gerente@fechacaixa.com",
            password="qualquer-123",
        )

    def tearDown(self):
        from django.core.cache import cache

        cache.clear()

    def test_usuario_logado_tambem_e_barrado(self):
        self.client.force_authenticate(user=self.usuario)

        for _ in range(5):
            resp = self.client.post(
                self.URL,
                {"codigo": "AURO-0000", "apelido": "Celular"},
                format="json",
            )
            self.assertEqual(resp.status_code, 401)

        resp = self.client.post(
            self.URL, {"codigo": "AURO-0000", "apelido": "Celular"}, format="json"
        )
        self.assertEqual(resp.status_code, 429)

    def test_sexta_tentativa_no_minuto_e_barrada(self):
        for _ in range(5):
            self.client.post(
                self.URL, {"codigo": "XXXX-1111", "apelido": "a"}, format="json"
            )

        resp = self.client.post(
            self.URL, {"codigo": "XXXX-1111", "apelido": "a"}, format="json"
        )

        self.assertEqual(resp.status_code, 429)

    def test_teto_vale_ate_para_o_codigo_certo(self):
        """O atacante nao pode usar a ultima tentativa certa para escapar."""
        for _ in range(5):
            self.client.post(
                self.URL, {"codigo": "XXXX-1111", "apelido": "a"}, format="json"
            )

        resp = self.client.post(
            self.URL,
            {"codigo": self.conta.codigo_acesso, "apelido": "Celular"},
            format="json",
        )

        self.assertEqual(resp.status_code, 429)


class FormularioFechadoTests(APITestCase):
    """Os cinco pontos que hoje respondem para qualquer um da internet."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        self.loja = criar_loja(
            nome_loja="Loja Centro", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.ana = Encarregado.objects.create(nome="Ana", conta=self.conta)
        self.outra_conta = Conta.objects.create(nome="Concorrente")
        self.loja_da_outra = criar_loja(
            nome_loja="Loja Rival", cidade="Patos", endereco="Rua 9",
            conta=self.outra_conta,
        )

    def entrar(self, conta=None):
        conta = conta or self.conta
        resp = self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": conta.codigo_acesso, "apelido": "Celular"},
            format="json",
        )
        return resp.cookies["formulario_token"].value

    def test_sem_token_nao_lista_lojas(self):
        resp = self.client.get(f"/api/v1/lojas/?conta={self.conta.slug}")

        self.assertEqual(resp.status_code, 401)

    def test_sem_token_nao_lista_responsaveis(self):
        resp = self.client.get(f"/api/v1/responsaveis-retirada/?conta={self.conta.slug}")

        self.assertEqual(resp.status_code, 401)

    def test_sem_token_nao_lanca_fechamento(self):
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            {
                "loja": str(self.loja.public_id),
                "lancado_por": str(self.ana.public_id),
                "periodo": "MANHA",
                "dinheiro": "10.00",
                "houve_retirada": False,
                "houve_despesa": False,
                "houve_desperdicio": False,
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 401)

    def test_sem_token_nao_pergunta_os_turnos(self):
        resp = self.client.get("/api/v1/fechamentos-caixa/turnos/")

        self.assertEqual(resp.status_code, 401)

    def test_sem_token_nao_abre_a_correcao(self):
        fechamento = FechamentoCaixa.objects.create(
            loja=self.loja,
            nome_funcionario="Ana",
            data=timezone.localdate(),
            periodo=FechamentoCaixa.Periodo.MANHA,
            dinheiro="10.00",
        )

        resp = self.client.get(
            f"/api/v1/fechamentos-caixa/{fechamento.public_id}/correcao/"
        )

        self.assertEqual(resp.status_code, 401)

    def test_com_token_lista_as_lojas_da_propria_conta(self):
        self.entrar()

        resp = self.client.get("/api/v1/lojas/")

        self.assertEqual(resp.status_code, 200)
        nomes = [loja["nome_loja"] for loja in resp.data["results"]]
        self.assertEqual(nomes, ["Loja Centro"])

    def test_token_de_uma_conta_nao_alcanca_a_outra(self):
        """O ?conta= da URL nao pode mais mandar em nada."""
        self.entrar()

        resp = self.client.get(f"/api/v1/lojas/?conta={self.outra_conta.slug}")

        nomes = [loja["nome_loja"] for loja in resp.data["results"]]
        self.assertNotIn("Loja Rival", nomes)

    def test_aparelho_revogado_perde_o_acesso(self):
        self.entrar()
        DispositivoDoFormulario.objects.update(revogado_em=timezone.now())

        resp = self.client.get("/api/v1/lojas/")

        self.assertEqual(resp.status_code, 401)

    def test_sair_revoga_o_proprio_aparelho(self):
        self.entrar()

        resp = self.client.post("/api/v1/formulario/sair/")

        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(DispositivoDoFormulario.objects.get().revogado_em)
        self.assertEqual(self.client.get("/api/v1/lojas/").status_code, 401)

    def test_empresa_responde_com_o_nome_para_o_titulo(self):
        self.entrar()

        resp = self.client.get("/api/v1/formulario/empresa/")

        self.assertEqual(resp.data["nome"], "Aurora Salgados")
        self.assertEqual(resp.data["fechamentos_por_dia"], 2)


class RegistrarUsoNoRequestTests(APITestCase):
    """TemAcessoAoFormulario chama registrar_uso em toda chamada — sem teste ate aqui."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": self.conta.codigo_acesso, "apelido": "Celular"},
            format="json",
        )
        # O cookie ja fica gravado no self.client depois do POST acima; as
        # chamadas seguintes reusam o mesmo aparelho.
        self.dispositivo = DispositivoDoFormulario.objects.get()

    def test_uso_antigo_e_atualizado_por_uma_chamada(self):
        antigo = timezone.now() - timedelta(hours=2)
        DispositivoDoFormulario.objects.update(ultimo_uso_em=antigo)

        self.client.get("/api/v1/formulario/empresa/")

        self.dispositivo.refresh_from_db()
        self.assertGreater(self.dispositivo.ultimo_uso_em, antigo)

    def test_uso_de_ha_um_minuto_nao_e_regravado_de_novo(self):
        recente = timezone.now() - timedelta(minutes=1)
        DispositivoDoFormulario.objects.update(ultimo_uso_em=recente)

        self.client.get("/api/v1/formulario/empresa/")

        self.dispositivo.refresh_from_db()
        self.assertEqual(self.dispositivo.ultimo_uso_em, recente)


class CorrecaoEntreEmpresasTests(APITestCase):
    """A janela de correcao e o caminho mais curto entre duas contas.

    Ela busca o lancamento pelo public_id, fora do get_queryset — e o id
    sozinho nao diz de quem e. Sem filtrar pela conta do aparelho, quem
    descobre o id de um fechamento da empresa vizinha le tudo e ainda
    reescreve por cima, mudando a loja para uma sua.
    """

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        self.minha_loja = criar_loja(
            nome_loja="Loja Centro", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.ana = Encarregado.objects.create(nome="Ana", conta=self.conta)
        self.outra_conta = Conta.objects.create(nome="Concorrente")
        self.loja_da_outra = criar_loja(
            nome_loja="Loja Rival", cidade="Patos", endereco="Rua 9",
            conta=self.outra_conta,
        )
        self.fechamento_da_outra = FechamentoCaixa.objects.create(
            loja=self.loja_da_outra,
            nome_funcionario="Funcionaria da rival",
            data=timezone.localdate(),
            periodo=FechamentoCaixa.Periodo.MANHA,
            dinheiro="100.00",
        )
        self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": self.conta.codigo_acesso, "apelido": "Celular"},
            format="json",
        )

    def url_da_outra(self):
        return f"/api/v1/fechamentos-caixa/{self.fechamento_da_outra.public_id}/correcao/"

    def test_nao_le_o_lancamento_de_outra_empresa(self):
        resp = self.client.get(self.url_da_outra())

        self.assertEqual(resp.status_code, 404)

    def test_nao_reescreve_o_lancamento_de_outra_empresa(self):
        resp = self.client.patch(
            self.url_da_outra(),
            {
                "loja": str(self.minha_loja.public_id),
                "nome_funcionario": "Sequestro",
                "periodo": "MANHA",
                "dinheiro": "999.00",
                "houve_retirada": False,
                "houve_despesa": False,
                "houve_desperdicio": False,
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 404)
        # O status sozinho nao prova nada: o que importa e a linha no banco.
        self.fechamento_da_outra.refresh_from_db()
        self.assertEqual(self.fechamento_da_outra.loja_id, self.loja_da_outra.id)
        self.assertEqual(self.fechamento_da_outra.nome_funcionario, "Funcionaria da rival")

    def test_lancar_em_loja_de_outra_empresa_e_recusado(self):
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            {
                "loja": str(self.loja_da_outra.public_id),
                "lancado_por": str(self.ana.public_id),
                "periodo": "MANHA",
                "dinheiro": "10.00",
                "houve_retirada": False,
                "houve_despesa": False,
                "houve_desperdicio": False,
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Esta loja nao e da sua empresa.", str(resp.data))


class AparelhoExpiraTests(APITestCase):
    """Os 180 dias precisam existir no servidor.

    So no max_age do cookie eles sao decorativos: quem copia o token de um
    aparelho manda o header na mao, sem cookie nenhum, e vale para sempre.
    """

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        criar_loja(
            nome_loja="Loja Centro", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": self.conta.codigo_acesso, "apelido": "Celular"},
            format="json",
        )

    def test_aparelho_velho_perde_o_acesso(self):
        nascimento = timezone.now() - DispositivoDoFormulario.VALIDADE - timedelta(days=1)
        # update() e nao save(): created_at e auto_now_add e ignora atribuicao.
        DispositivoDoFormulario.objects.update(created_at=nascimento)

        resp = self.client.get("/api/v1/formulario/empresa/")

        self.assertEqual(resp.status_code, 401)

    def test_aparelho_dentro_da_validade_continua_entrando(self):
        nascimento = timezone.now() - DispositivoDoFormulario.VALIDADE + timedelta(days=1)
        DispositivoDoFormulario.objects.update(created_at=nascimento)

        resp = self.client.get("/api/v1/formulario/empresa/")

        self.assertEqual(resp.status_code, 200)


class PeriodoDaEmpresaTests(APITestCase):
    """Empresa de um fechamento por dia nao tem manha nem tarde.

    Gravar o caixa do dia inteiro como MANHA faria o painel mentir: ele
    mostraria "Manha" para um numero que e do dia todo, e cobraria uma tarde
    que nunca vai chegar.
    """

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.conta = Conta.objects.create(nome="Uma Vez", fechamentos_por_dia=1)
        self.loja = criar_loja(
            nome_loja="Unica", cidade="Patos", endereco="Rua 1", conta=self.conta
        )
        self.ana = Encarregado.objects.create(nome="Ana", conta=self.conta)
        self.entrar(self.conta)

    def entrar(self, conta):
        self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": conta.codigo_acesso, "apelido": "Celular"},
            format="json",
        )

    def payload(self, periodo, loja=None):
        return {
            "loja": str((loja or self.loja).public_id),
            "lancado_por": str(self.ana.public_id),
            "periodo": periodo,
            "dinheiro": "10.00",
            "houve_retirada": False,
            "houve_despesa": False,
            "houve_desperdicio": False,
        }

    def test_aceita_dia(self):
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/", self.payload("DIA"), format="json"
        )

        self.assertEqual(resp.status_code, 201)

    def test_recusa_manha(self):
        resp = self.client.post(
            "/api/v1/fechamentos-caixa/", self.payload("MANHA"), format="json"
        )

        self.assertEqual(resp.status_code, 400)

    def test_empresa_de_dois_turnos_recusa_dia(self):
        conta = Conta.objects.create(nome="Dois Turnos")
        loja = criar_loja(
            nome_loja="Centro", cidade="Patos", endereco="Rua 2", conta=conta
        )
        self.entrar(conta)

        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            # Uma quarta-feira comum: no domingo e no feriado "DIA" e o turno
            # certo ate para quem fecha duas vezes, e o teste provaria o
            # contrario do que diz.
            {**self.payload("DIA", loja=loja), "data": "2026-08-05"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)

    def test_domingo_nao_atrapalha_quem_fecha_uma_vez_por_dia(self):
        """A regra de turno unico de domingo tira manha e tarde e poe "DIA".

        Numa empresa que so tem "DIA" nao ha nada para tirar: aplicar a regra
        aqui seria pedir de novo o que ela ja manda todo dia.
        """
        # O domingo passado, e nao o proximo: data no futuro e recusada por
        # outra regra, e o teste passaria a provar a regra errada.
        hoje = timezone.localdate()
        domingo = hoje - timedelta(days=(hoje.weekday() + 1) % 7)

        resp = self.client.post(
            "/api/v1/fechamentos-caixa/",
            {**self.payload("DIA"), "data": domingo.isoformat()},
            format="json",
        )

        self.assertEqual(resp.status_code, 201)

    def test_turnos_oferece_so_o_dia_inteiro(self):
        """O formulario pergunta ao backend quais periodos existem.

        Se "DIA" aparecesse ao lado de manha e tarde, a lista de periodos
        viraria a uniao de dois modelos de operacao que nunca convivem.
        """
        resp = self.client.get("/api/v1/fechamentos-caixa/turnos/")

        self.assertEqual(resp.data["periodos"], ["DIA"])
        self.assertIsNone(resp.data["motivo"])
