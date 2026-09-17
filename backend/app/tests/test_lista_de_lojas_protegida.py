"""A lista de lojas exige o aparelho ter entrado com o codigo da empresa.

Herdeiro de test_seguranca.py, que cobria o relatorio PDF, notificacoes,
estoque e movimentacoes — tudo do Unistock, que saiu. Este teste era o unico
de la que fala do FechaCaixa, e e o que guarda o furo mais caro que este
projeto ja teve: /lojas/?conta=<slug> era publico, e devolvia as lojas de
qualquer empresa para quem soubesse o apelido dela.
"""

from rest_framework.test import APITestCase

from app.models import Conta

from .fabricas import criar_loja


class ListaDeLojasProtegidaTests(APITestCase):
    def setUp(self):
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        criar_loja(
            conta=self.conta, nome_loja="Loja A", cidade="Patos", endereco="Rua 1"
        )

    def test_sem_o_aparelho_liberado_nao_lista(self):
        resp = self.client.get("/api/v1/lojas/")

        self.assertEqual(resp.status_code, 401)

    def test_o_slug_da_empresa_na_url_nao_abre_nada(self):
        """Era exatamente assim que vazava: bastava saber o apelido."""
        resp = self.client.get(f"/api/v1/lojas/?conta={self.conta.slug}")

        self.assertEqual(resp.status_code, 401)
