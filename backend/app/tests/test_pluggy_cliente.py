from datetime import date
import io
import urllib.error
from unittest.mock import patch

from django.test import SimpleTestCase

from app.services.pluggy.cliente import ClientePluggy, FalhaDaPluggy


class ClientePluggyTests(SimpleTestCase):
    def setUp(self):
        self.cliente = ClientePluggy("id", "segredo")

    def test_autentica_uma_vez_e_usa_a_chave(self):
        with patch.object(
            ClientePluggy,
            "_pedir",
            side_effect=[{"apiKey": "k1"}, {"id": "i1"}, {"results": [{"id": "a1"}]}],
        ) as pedir:
            self.assertEqual(self.cliente.item("i1"), {"id": "i1"})
            self.assertEqual(self.cliente.contas("i1"), [{"id": "a1"}])
        chamadas = [c.args[:2] for c in pedir.call_args_list]
        self.assertEqual(
            chamadas,
            [("POST", "/auth"), ("GET", "/items/i1"), ("GET", "/accounts")],
        )
        self.assertEqual(
            pedir.call_args_list[0].args[2],
            {"clientId": "id", "clientSecret": "segredo"},
        )
        self.assertEqual(self.cliente._api_key, "k1")

    def test_transacoes_usa_v2_com_datefrom_e_dateto(self):
        with patch.object(
            ClientePluggy,
            "_pedir",
            side_effect=[{"apiKey": "k"}, {"results": [{"id": "t1"}], "next": None}],
        ) as pedir:
            r = self.cliente.transacoes("a1", date(2026, 10, 11), date(2026, 10, 18))
        self.assertEqual(r, [{"id": "t1"}])
        chamada = pedir.call_args_list[1]
        self.assertEqual(chamada.args[:2], ("GET", "/v2/transactions"))
        self.assertEqual(
            chamada.kwargs["params"],
            {"accountId": "a1", "dateFrom": "2026-10-11", "dateTo": "2026-10-18"},
        )

    def test_segue_o_next_ate_acabar(self):
        url = "https://api.pluggy.ai/v2/transactions?accountId=a1&page=2"
        with patch.object(
            ClientePluggy,
            "_pedir",
            side_effect=[
                {"apiKey": "k"},
                {"results": [{"id": "t1"}], "next": url},
                {"results": [{"id": "t2"}], "next": "cur3"},
                {"results": [{"id": "t3"}], "next": None},
            ],
        ) as pedir:
            r = self.cliente.transacoes("a1", date(2026, 10, 11), date(2026, 10, 18))
        self.assertEqual([t["id"] for t in r], ["t1", "t2", "t3"])
        c2, c3 = pedir.call_args_list[2], pedir.call_args_list[3]
        self.assertEqual(c2.args[1], "/v2/transactions?accountId=a1&page=2")
        self.assertIsNone(c2.kwargs.get("params"))
        self.assertEqual(c3.args[1], "/v2/transactions")
        self.assertEqual(c3.kwargs["params"]["after"], "cur3")
        self.assertEqual(c3.kwargs["params"]["accountId"], "a1")

    def _http(self, codigo):
        return urllib.error.HTTPError("u", codigo, "x", {}, io.BytesIO(b"{}"))

    def test_401_vira_falha_com_mensagem_da_tela(self):
        with patch("urllib.request.urlopen", side_effect=self._http(401)):
            with self.assertRaises(FalhaDaPluggy) as ctx:
                self.cliente.item("i1")
        self.assertEqual(ctx.exception.status, 401)
        self.assertEqual(
            ctx.exception.mensagem, "A Pluggy recusou as chaves desta conexão."
        )

    def test_outras_falhas_http_e_de_rede(self):
        casos = [
            (self._http(403), "A Pluggy recusou as chaves desta conexão."),
            (self._http(404), "A Pluggy não achou o item ou a conta (Item ID errado?)."),
            (self._http(500), "A Pluggy respondeu 500."),
            (urllib.error.URLError("sem rede"), "A Pluggy não respondeu."),
            (TimeoutError(), "A Pluggy não respondeu."),
        ]
        for erro, mensagem in casos:
            with self.subTest(mensagem=mensagem):
                with patch("urllib.request.urlopen", side_effect=erro):
                    with self.assertRaises(FalhaDaPluggy) as ctx:
                        ClientePluggy("i", "s").item("x")
                self.assertEqual(ctx.exception.mensagem, mensagem)
