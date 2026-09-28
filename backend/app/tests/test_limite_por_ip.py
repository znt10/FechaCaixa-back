"""O limite por IP conta o visitante, e nao o servidor do front.

Todo pedido chega pelo rewrite /backend do Next, que chama a URL publica da
API. O ultimo IP do X-Forwarded-For e, entao, o do servidor do front, igual
para todo visitante (SERVIDOR abaixo). O IP de verdade vem em X-Cliente-IP,
e so vale com o PROXY_SEGREDO. Ver docs/limite-por-ip.md.

A rota usada e o POST /login/ (10/min): 10 tentativas passam, a 11a e 429.
"""

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

SEGREDO = "segredo-do-proxy-so-de-teste"
SERVIDOR = "187.0.0.1"
TETO_DO_LOGIN = 10


@override_settings(PROXY_SEGREDO=SEGREDO)
class LimitePorIPTests(APITestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _entrar(self, **cabecalhos):
        return self.client.post(
            "/login/",
            {"email": "ninguem@x.com", "password": "errada"},
            format="json",
            **cabecalhos,
        )

    def _pelo_next(self, ip, segredo=SEGREDO):
        """Como o pedido chega em producao: pelo Traefik, vindo do Next."""
        return self._entrar(
            HTTP_X_FORWARDED_FOR=f"{ip}, {SERVIDOR}",
            HTTP_X_CLIENTE_IP=ip,
            HTTP_X_PROXY_SEGREDO=segredo,
        )

    def _esgotar(self, fazer):
        for _ in range(TETO_DO_LOGIN):
            self.assertNotEqual(fazer().status_code, 429)

    def test_o_mesmo_ip_bate_no_teto(self):
        self._esgotar(lambda: self._pelo_next("200.1.1.1"))

        self.assertEqual(self._pelo_next("200.1.1.1").status_code, 429)

    def test_visitantes_diferentes_nao_dividem_o_contador(self):
        """Sem o X-Cliente-IP, os dois cairiam no IP do SERVIDOR."""
        self._esgotar(lambda: self._pelo_next("200.1.1.1"))

        self.assertNotEqual(self._pelo_next("200.2.2.2").status_code, 429)

    def test_trocar_o_comeco_do_x_forwarded_for_nao_dribla_o_teto(self):
        """O comeco da lista e o que o cliente escreveu; o fim, o Traefik."""
        contador = iter(range(1000))

        def forjado():
            return self._entrar(
                HTTP_X_FORWARDED_FOR=f"6.6.6.{next(contador)}, 200.1.1.1"
            )

        self._esgotar(forjado)

        self.assertEqual(forjado().status_code, 429)

    def test_sem_o_segredo_o_x_cliente_ip_e_ignorado(self):
        """Mandado de fora, o IP inventado nao vale: conta o do SERVIDOR."""
        contador = iter(range(1000))

        def forjado():
            return self._pelo_next(f"6.6.6.{next(contador)}", segredo="chute")

        self._esgotar(forjado)

        self.assertEqual(forjado().status_code, 429)

    @override_settings(PROXY_SEGREDO="")
    def test_segredo_vazio_nao_aceita_nada(self):
        """Sem segredo configurado, nem um cabecalho de segredo vazio passa."""
        contador = iter(range(1000))

        def forjado():
            return self._pelo_next(f"6.6.6.{next(contador)}", segredo="")

        self._esgotar(forjado)

        self.assertEqual(forjado().status_code, 429)

    def test_segredo_com_acento_e_recusado_sem_500(self):
        resposta = self._pelo_next("200.1.1.1", segredo="segrédo")

        self.assertNotEqual(resposta.status_code, 500)
