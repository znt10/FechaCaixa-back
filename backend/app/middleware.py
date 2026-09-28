"""Middleware do projeto."""

import hmac

from django.conf import settings
from django.http import HttpResponse


class IpDoProxyMiddleware:
    """Poe no X-Forwarded-For o IP do visitante que o Next mandou, SE vier com
    o segredo certo.

    Todo pedido do navegador chega aqui pelo rewrite /backend do Next, que
    chama a URL publica da API: Traefik -> Next -> internet -> Traefik ->
    Django. O ultimo IP da lista e, entao, o do proprio servidor do front, o
    mesmo para todo visitante, e o limite por IP juntaria o site inteiro num
    contador so. O Next repassa o IP de verdade em X-Cliente-IP, com o
    PROXY_SEGREDO em X-Proxy-Segredo.

    Sem o segredo o cabecalho e ignorado e vale o que o Traefik escreveu no
    fim da lista (NUM_PROXIES = 1). Os dois sao apagados de qualquer jeito,
    para nenhuma view ler um valor que veio de fora.
    """

    IP = "HTTP_X_CLIENTE_IP"
    SEGREDO = "HTTP_X_PROXY_SEGREDO"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request) -> HttpResponse:
        ip = request.META.pop(self.IP, "").strip()
        recebido = request.META.pop(self.SEGREDO, "")
        # Lido a cada pedido, e nao no __init__: trocar o segredo no ambiente
        # tem que valer sem depender de quando o worker subiu.
        segredo = settings.PROXY_SEGREDO
        # compare_digest em bytes: com str ele lanca TypeError para texto nao
        # ASCII, e um cabecalho forjado viraria 500 em vez de ser ignorado.
        if segredo and ip and hmac.compare_digest(recebido.encode(), segredo.encode()):
            request.META["HTTP_X_FORWARDED_FOR"] = ip
        return self.get_response(request)


class SemCacheNaApi:
    """Manda o navegador nao guardar nenhuma resposta da API.

    Sem isto, DRF responde sem Cache-Control nenhum — e uma resposta sem
    informacao de validade pode ser guardada pelo proprio navegador por um
    tempo que ele decide sozinho (a "heuristica" do RFC 9111). Foi o que
    aconteceu no celular da loja: a gerente cadastrava uma loja, desativava
    quem retira dinheiro, e o seletor continuava com a lista velha mesmo
    depois de recarregar a pagina. O pedido nao estava sendo respondido com
    dado velho — ele nao estava saindo do aparelho.

    no-store, e nao no-cache: no-cache ainda deixa guardar a copia, so obriga
    a revalidar. Aqui o conteudo e de uma empresa so, muitas vezes num
    aparelho compartilhado no balcao, e nao ha motivo para ele ficar em disco.

    So /api/: fora dali estao o /admin/ do Django e os estaticos, que tem a
    propria politica — o admin, por exemplo, ja manda no-store por conta
    propria, e os estaticos PRECISAM ser guardados.
    """

    PREFIXO = "/api/"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request) -> HttpResponse:
        response = self.get_response(request)

        if request.path.startswith(self.PREFIXO):
            response["Cache-Control"] = "no-store"

        return response
