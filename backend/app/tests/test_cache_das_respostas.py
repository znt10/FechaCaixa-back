"""A API precisa dizer, em toda resposta, que ninguem deve guardar.

O sintoma foi este: a gerente cadastrava uma loja, desativava quem retira
dinheiro, e o celular da loja continuava mostrando a lista antiga — inclusive
depois de recarregar a pagina.

A causa nao estava no React Query. Estava mais embaixo: DRF nao manda
Cache-Control nenhum, e uma resposta sem informacao de validade pode ser
guardada pelo proprio navegador por um tempo que ele decide sozinho (a
"heuristica" do RFC 9111). Com isso o pedido nem chegava a sair do aparelho —
nenhuma correcao no cliente alcanca uma requisicao que nao acontece.

no-store, e nao no-cache: no-cache ainda permite guardar a copia (so obriga a
revalidar), e aqui o conteudo e de uma empresa so, muitas vezes num aparelho
compartilhado no balcao. Nao ha motivo para ficar em disco.
"""

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from rest_framework.test import APITestCase

from app.models import Conta

from .fabricas import criar_loja, vincular_conta


class CacheControlDasRespostasTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        self.gerente = User.objects.create_user(
            username="ger@aurora.com", email="ger@aurora.com", password="x"
        )
        self.gerente.groups.add(Group.objects.get_or_create(name="Gerente")[0])
        vincular_conta(self.gerente, self.conta)
        criar_loja(
            conta=self.conta, nome_loja="Loja A", cidade="Patos", endereco="Rua 1"
        )
        self.client.force_authenticate(user=self.gerente)

    def test_a_lista_de_lojas_proibe_guardar(self):
        resp = self.client.get("/api/v1/lojas/")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("no-store", resp.headers.get("Cache-Control", ""))

    def test_a_lista_de_quem_retira_proibe_guardar(self):
        resp = self.client.get("/api/v1/responsaveis-retirada/")

        self.assertIn("no-store", resp.headers.get("Cache-Control", ""))

    def test_o_painel_proibe_guardar(self):
        resp = self.client.get("/api/v1/fechamentos-caixa/")

        self.assertIn("no-store", resp.headers.get("Cache-Control", ""))

    def test_vale_para_quem_nao_esta_logado_tambem(self):
        """O formulario da loja e anonimo — e e onde o bug aparecia."""
        self.client.force_authenticate(user=None)

        resp = self.client.get("/api/v1/lojas/")

        self.assertIn("no-store", resp.headers.get("Cache-Control", ""))

    def test_nao_mexe_no_que_nao_e_api(self):
        """Fora de /api/ a resposta sai como veio.

        Testado direto no middleware porque nao da para testar pela URL: o
        /admin/ do Django ja manda no-store por conta propria, entao o
        cabecalho estaria la de qualquer jeito e o teste passaria sem provar
        nada. O que importa aqui e que os estaticos, que PRECISAM ser
        guardados, continuem podendo.
        """
        from django.http import HttpResponse
        from django.test import RequestFactory

        from app.middleware import SemCacheNaApi

        def responder(_request):
            resposta = HttpResponse()
            resposta["Cache-Control"] = "max-age=31536000"
            return resposta

        middleware = SemCacheNaApi(responder)
        pedido = RequestFactory().get("/static/admin/css/base.css")

        resposta = middleware(pedido)

        self.assertEqual(resposta["Cache-Control"], "max-age=31536000")
