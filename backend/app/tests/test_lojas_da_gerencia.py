"""A gerente administrando as lojas da propria empresa.

A regra antiga era do Unistock: gerente so escrevia na loja em que ele fosse
`Loja.gerente`. Isso fazia sentido num painel com varios gerentes dividindo as
lojas de uma rede. No FechaCaixa a gerente e a dona da operacao da empresa
dela, e as lojas que ja existiam nasceram sem gerente atribuido — ou seja, a
regra antiga a deixava de fora das proprias lojas.

Apagar e outra conversa, e por isso tem teste separado: FechamentoCaixa.loja e
CASCADE. Apagar uma loja com lancamentos apagaria o caixa dela junto, sem
volta.
"""

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from rest_framework.test import APITestCase

from app.models import Conta, FechamentoCaixa, Loja

from .fabricas import criar_loja, vincular_conta


class BaseDaGerente(APITestCase):
    def setUp(self):
        cache.clear()
        self.conta = Conta.objects.create(nome="Aurora Salgados")
        self.outra = Conta.objects.create(nome="Concorrente")

        self.gerente = User.objects.create_user(
            username="ger@aurora.com", email="ger@aurora.com", password="x"
        )
        self.gerente.groups.add(Group.objects.get_or_create(name="Gerente")[0])
        vincular_conta(self.gerente, self.conta)

        # Sem gerente atribuido de proposito: e o estado das lojas que ja
        # existiam quando a tela da empresa passou a ser da gerente.
        self.loja = criar_loja(
            conta=self.conta, nome_loja="Loja A", cidade="Patos", endereco="Rua 1"
        )
        self.client.force_authenticate(user=self.gerente)


class AdministrarLojasTests(BaseDaGerente):
    def test_desativa_loja_que_nao_esta_atribuida_a_ela(self):
        """O botao "Desativar" da tela da empresa respondia 403 aqui."""
        resp = self.client.patch(
            f"/api/v1/lojas/{self.loja.public_id}/", {"ativo": False}, format="json"
        )

        self.assertEqual(resp.status_code, 200)
        self.loja.refresh_from_db()
        self.assertFalse(self.loja.ativo)

    def test_nao_desativa_loja_de_outra_empresa(self):
        vizinha = criar_loja(
            conta=self.outra, nome_loja="Rival", cidade="Patos", endereco="Rua 2"
        )

        resp = self.client.patch(
            f"/api/v1/lojas/{vizinha.public_id}/", {"ativo": False}, format="json"
        )

        self.assertIn(resp.status_code, (403, 404))
        vizinha.refresh_from_db()
        self.assertTrue(vizinha.ativo)


class ApagarLojaTests(BaseDaGerente):
    def test_apaga_loja_desativada_e_sem_lancamento(self):
        """O caso real: cadastrou errado e quer sumir com ela."""
        self.loja.ativo = False
        self.loja.save(update_fields=["ativo"])

        resp = self.client.delete(f"/api/v1/lojas/{self.loja.public_id}/")

        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Loja.objects.filter(id=self.loja.id).exists())

    def test_apaga_loja_ativa_e_sem_lancamento(self):
        """"Desative antes de apagar" e a sequencia da TELA, nao do servidor.

        Escrevi este teste ao contrario primeiro — exigindo 400 para loja
        ativa — e ele quebrou dois testes antigos: o Admin apaga loja direto
        desde sempre (test_lojas.LojaDeleteTests). O pedido era sobre onde o
        botao aparece; virar regra do sistema foi invencao minha.
        """
        resp = self.client.delete(f"/api/v1/lojas/{self.loja.public_id}/")

        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Loja.objects.filter(id=self.loja.id).exists())

    def test_nao_apaga_loja_que_tem_caixa_lancado(self):
        """FechamentoCaixa.loja e CASCADE: apagar levaria o caixa junto.

        Este e o unico ponto do sistema onde uma acao de tela poderia destruir
        contabilidade. Desativada ela ja some do painel e do formulario, entao
        nao existe motivo para apagar de verdade.
        """
        self.loja.ativo = False
        self.loja.save(update_fields=["ativo"])
        FechamentoCaixa.objects.create(
            loja=self.loja, data="2026-08-20", periodo="MANHA", dinheiro=100
        )

        resp = self.client.delete(f"/api/v1/lojas/{self.loja.public_id}/")

        self.assertEqual(resp.status_code, 400)
        self.assertTrue(Loja.objects.filter(id=self.loja.id).exists())
        self.assertEqual(FechamentoCaixa.objects.filter(loja=self.loja).count(), 1)

    def test_a_mensagem_diz_quantos_lancamentos_seguram_a_loja(self):
        """Quem le precisa saber por que nao deu, e quanto ha ali."""
        self.loja.ativo = False
        self.loja.save(update_fields=["ativo"])
        for dia in ("2026-08-20", "2026-08-21"):
            FechamentoCaixa.objects.create(
                loja=self.loja, data=dia, periodo="MANHA", dinheiro=10
            )

        resp = self.client.delete(f"/api/v1/lojas/{self.loja.public_id}/")

        self.assertIn("2", str(resp.data))

    def test_nao_apaga_loja_de_outra_empresa(self):
        vizinha = criar_loja(
            conta=self.outra, nome_loja="Rival", cidade="Patos",
            endereco="Rua 2", ativo=False,
        )

        resp = self.client.delete(f"/api/v1/lojas/{vizinha.public_id}/")

        self.assertIn(resp.status_code, (403, 404))
        self.assertTrue(Loja.objects.filter(id=vizinha.id).exists())
