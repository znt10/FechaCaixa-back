"""Cliente HTTP da API da Pluggy (v2). So _pedir toca a rede."""
import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.pluggy.ai"
TIMEOUT = 60


class FalhaDaPluggy(Exception):
    def __init__(self, status, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem


def _mensagem_http(codigo):
    if codigo in (401, 403):
        return "A Pluggy recusou as chaves desta conexão."
    if codigo == 404:
        return "A Pluggy não achou o item ou a conta (Item ID errado?)."
    return f"A Pluggy respondeu {codigo}."


class ClientePluggy:
    def __init__(self, client_id, client_secret):
        self._client_id = client_id
        self._client_secret = client_secret
        self._api_key = None

    def _pedir(self, metodo, caminho, corpo=None, params=None):
        url = BASE + caminho
        if params:
            url += ("&" if "?" in caminho else "?") + urllib.parse.urlencode(params)
        cabecalhos = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            cabecalhos["X-API-KEY"] = self._api_key
        dados = json.dumps(corpo).encode() if corpo is not None else None
        req = urllib.request.Request(url, data=dados, headers=cabecalhos, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                texto = resp.read().decode()
        except urllib.error.HTTPError as e:
            raise FalhaDaPluggy(e.code, _mensagem_http(e.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise FalhaDaPluggy(None, "A Pluggy não respondeu.") from None
        try:
            return json.loads(texto) if texto else {}
        except ValueError:
            raise FalhaDaPluggy(None, "A Pluggy não respondeu.") from None

    def _autenticar(self):
        if self._api_key is None:
            r = self._pedir(
                "POST",
                "/auth",
                {"clientId": self._client_id, "clientSecret": self._client_secret},
            )
            self._api_key = r["apiKey"]

    def _get(self, caminho, params=None):
        self._autenticar()
        return self._pedir("GET", caminho, params=params)

    def item(self, item_id):
        return self._get(f"/items/{item_id}")

    def contas(self, item_id):
        return self._get("/accounts", {"itemId": item_id}).get("results", [])

    def transacoes(self, account_id, de, ate):
        params = {
            "accountId": account_id,
            "dateFrom": de.isoformat(),
            "dateTo": ate.isoformat(),
        }
        caminho = "/v2/transactions"
        pedido = params
        resultados = []
        # Um `next` que ja veio pediria a mesma pagina de novo, sem fim.
        vistos = set()
        while True:
            pagina = self._get(caminho, pedido)
            resultados.extend(pagina.get("results", []))
            proximo = pagina.get("next")
            if not proximo:
                return resultados
            if proximo in vistos:
                raise FalhaDaPluggy(None, "A Pluggy repetiu a mesma página de transações.")
            vistos.add(proximo)
            if proximo.startswith("http"):
                partes = urllib.parse.urlparse(proximo)
                caminho = partes.path + ("?" + partes.query if partes.query else "")
                pedido = None
            else:
                caminho = "/v2/transactions"
                pedido = {**params, "after": proximo}
