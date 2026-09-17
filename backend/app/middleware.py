"""Middleware do projeto."""

from django.http import HttpResponse


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
