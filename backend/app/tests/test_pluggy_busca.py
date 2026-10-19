import datetime
from unittest import mock

from cryptography.fernet import Fernet
from django.core import mail
from django.test import TestCase, override_settings

from app.models import ConexaoPluggy, ContaBancaria, TransacaoBancaria
from app.services.pluggy import busca
from app.services.pluggy.cifra import cifrar
from app.services.pluggy.cliente import FalhaDaPluggy

from .fabricas import conta_padrao, criar_loja

CHAVE = Fernet.generate_key().decode()
AGORA = datetime.datetime(2026, 10, 18, 15, 0, tzinfo=datetime.timezone.utc)
RECENTE = "2026-10-18T10:00:00.000Z"
ASSUNTO_CAIU = (
    "Pluggy caiu: Delícias Salgados — A autorização no banco venceu ou foi "
    "cancelada. Reconecte a conta no Meu Pluggy."
)


def transacao(id_, valor="-10.00", dia="2026-10-17T15:00:00.000Z"):
    return {
        "id": id_,
        "status": "POSTED",
        "date": dia,
        "amount": float(valor),
        "description": f"PIX {id_}",
    }


class ClienteFalso:
    def __init__(self, status="UPDATED", atualizado=RECENTE, transacoes=None, falha=None):
        self.status = status
        self.atualizado = atualizado
        self.lista = transacoes if transacoes is not None else []
        self.falha = falha
        self.pedidos = []

    def item(self, item_id):
        if self.falha:
            raise self.falha
        return {"status": self.status, "lastUpdatedAt": self.atualizado}

    def contas(self, item_id):
        return []

    def transacoes(self, account_id, de, ate):
        self.pedidos.append((account_id, de, ate))
        return self.lista


class ClienteQueQuebraNoExtrato(ClienteFalso):
    """Responde os itens e levanta `erro` ao pedir o extrato."""

    def __init__(self, erro, **campos):
        super().__init__(**campos)
        self.erro = erro

    def transacoes(self, account_id, de, ate):
        self.pedidos.append((account_id, de, ate))
        raise self.erro


@override_settings(
    PLUGGY_CHAVE_CIFRA=CHAVE, PLUGGY_EMAIL_AVISO="dono@x.io", FRONTEND_URL="https://f"
)
class BuscaPluggyTests(TestCase):
    def setUp(self):
        self.titular = conta_padrao()
        self.titular.nome = "Delícias Salgados"
        self.titular.save()
        self.loja = criar_loja(nome_loja="Centro")
        self.conta_bancaria = ContaBancaria.objects.create(
            loja=self.loja, banco="SANTANDER", agencia="1", numero="2",
            pluggy_account_id="acc1",
        )
        self.conexao = ConexaoPluggy.objects.create(
            conta=self.titular, email="a@b.c", client_id="cid",
            client_secret_cifrado=cifrar("segredo"), item_ids="item1",
            estado=ConexaoPluggy.Estado.ATIVA,
        )
        self.conta_bancaria.conexao_pluggy = self.conexao
        self.conta_bancaria.save()

    def rodar(self, cliente, agora=AGORA):
        resultado = busca.buscar_conexao(self.conexao, agora=agora, cliente=cliente)
        self.conexao.refresh_from_db()
        return resultado

    def test_primeira_busca_boa_grava_e_avisa_conectada(self):
        cliente = ClienteFalso(transacoes=[transacao("t1"), transacao("t2")])
        resultado = self.rodar(cliente)
        self.assertEqual(resultado.novas, 2)
        self.assertEqual(TransacaoBancaria.objects.count(), 2)
        self.assertEqual(self.conexao.conectada_em, AGORA)
        self.assertEqual(self.conexao.ultima_busca_ok_em, AGORA)
        self.assertEqual(self.conexao.itens["item1"]["status"], "UPDATED")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject, "Pluggy conectada: Delícias Salgados — 2 transações"
        )
        self.assertEqual(mail.outbox[0].to, ["dono@x.io"])
        self.assertIn("https://f/plataforma/pluggy", mail.outbox[0].body)

    def test_segunda_busca_boa_nao_avisa_de_novo(self):
        self.rodar(ClienteFalso(transacoes=[transacao("t1")]))
        self.rodar(ClienteFalso(transacoes=[transacao("t1")]))
        self.assertEqual(len(mail.outbox), 1)

    def test_primeira_busca_pede_60_dias_e_as_seguintes_7(self):
        primeiro = ClienteFalso(transacoes=[transacao("t1")])
        self.rodar(primeiro)
        self.assertEqual(
            primeiro.pedidos, [("acc1", datetime.date(2026, 8, 19), datetime.date(2026, 10, 18))]
        )
        segundo = ClienteFalso()
        self.rodar(segundo)
        self.assertEqual(
            segundo.pedidos, [("acc1", datetime.date(2026, 10, 11), datetime.date(2026, 10, 18))]
        )

    def test_tres_falhas_seguidas_derrubam_e_avisam_uma_vez(self):
        self.conexao.conectada_em = AGORA
        self.conexao.save()
        falha = ClienteFalso(falha=FalhaDaPluggy(None, "A Pluggy não respondeu."))
        for _ in range(2):
            self.rodar(falha)
        self.assertEqual(self.conexao.estado, ConexaoPluggy.Estado.ATIVA)
        self.assertEqual(len(mail.outbox), 0)
        self.rodar(falha)
        self.assertEqual(self.conexao.estado, ConexaoPluggy.Estado.COM_ERRO)
        self.assertEqual(self.conexao.falhas_seguidas, 3)
        self.assertEqual(len(mail.outbox), 1)
        self.rodar(falha)
        self.assertEqual(len(mail.outbox), 1)

    def test_item_fora_de_updated_derruba_com_o_motivo(self):
        self.conexao.conectada_em = AGORA
        self.conexao.save()
        self.rodar(ClienteFalso(status="LOGIN_ERROR"))
        self.assertEqual(self.conexao.estado, ConexaoPluggy.Estado.COM_ERRO)
        self.assertEqual(mail.outbox[-1].subject, ASSUNTO_CAIU)

    def test_item_atualizando_nao_derruba_nem_avisa(self):
        self.conexao.conectada_em = AGORA
        self.conexao.save()
        for status in ("UPDATING", "CREATED"):
            self.rodar(ClienteFalso(status=status))
            self.assertEqual(self.conexao.estado, ConexaoPluggy.Estado.ATIVA)
        self.assertEqual(len(mail.outbox), 0)

    def test_item_parado_ha_mais_de_48h_derruba(self):
        self.conexao.conectada_em = AGORA
        self.conexao.save()
        self.rodar(ClienteFalso(atualizado="2026-10-15T12:00:00.000Z"))
        self.assertEqual(self.conexao.estado, ConexaoPluggy.Estado.COM_ERRO)
        self.assertEqual(
            mail.outbox[-1].subject,
            "Pluggy caiu: Delícias Salgados — O Meu Pluggy não atualiza desde 15/10 às 09:00.",
        )

    def test_volta_avisa_voltou_e_limpa_o_erro(self):
        self.conexao.conectada_em = AGORA
        self.conexao.save()
        self.rodar(ClienteFalso(status="LOGIN_ERROR"))
        self.rodar(ClienteFalso())
        self.assertEqual(self.conexao.estado, ConexaoPluggy.Estado.ATIVA)
        self.assertEqual(self.conexao.ultimo_erro, "")
        self.assertEqual(mail.outbox[-1].subject, "Pluggy voltou: Delícias Salgados")
        self.assertEqual(len(mail.outbox), 2)

    def test_conta_bancaria_desativada_e_pulada_sem_falha(self):
        self.conta_bancaria.ativo = False
        self.conta_bancaria.save()
        cliente = ClienteFalso(transacoes=[transacao("t1")])
        self.rodar(cliente)
        self.assertEqual(cliente.pedidos, [])
        self.assertEqual(self.conexao.falhas_seguidas, 0)

    def test_buscar_todas_ignora_configurando_e_desligada(self):
        for estado in (ConexaoPluggy.Estado.CONFIGURANDO, ConexaoPluggy.Estado.DESLIGADA):
            self.conexao.estado = estado
            self.conexao.save()
            self.assertEqual(busca.buscar_todas(agora=AGORA), [])

    def test_buscar_todas_roda_ativa_e_com_erro(self):
        self.conexao.client_secret_cifrado = "lixo"
        self.conexao.save()
        resultados = busca.buscar_todas(agora=AGORA)
        self.assertEqual(len(resultados), 1)
        self.assertEqual(resultados[0].conexao.falhas_seguidas, 1)

    def test_chave_trocada_conta_como_falha_sem_quebrar(self):
        self.conexao.client_secret_cifrado = cifrar("segredo")
        self.conexao.save()
        with override_settings(PLUGGY_CHAVE_CIFRA=Fernet.generate_key().decode()):
            busca.buscar_conexao(self.conexao, agora=AGORA)
        self.conexao.refresh_from_db()
        self.assertEqual(self.conexao.falhas_seguidas, 1)
        self.assertEqual(
            self.conexao.ultimo_erro,
            "Não foi possível ler o Client Secret salvo. Cadastre de novo na Plataforma.",
        )

    @override_settings(PLUGGY_CHAVE_CIFRA="")
    def test_sem_chave_de_cifra_conta_como_falha(self):
        busca.buscar_conexao(self.conexao, agora=AGORA)
        self.conexao.refresh_from_db()
        self.assertEqual(self.conexao.falhas_seguidas, 1)
        self.assertEqual(
            self.conexao.ultimo_erro,
            "Defina PLUGGY_CHAVE_CIFRA no servidor antes de ligar a Pluggy.",
        )

    @override_settings(PLUGGY_EMAIL_AVISO="", MENSALIDADE_EMAIL_AVISO="mensalidade@x.io")
    def test_sem_email_proprio_avisa_no_da_mensalidade(self):
        self.rodar(ClienteFalso(transacoes=[transacao("t1")]))
        self.assertEqual(mail.outbox[0].to, ["mensalidade@x.io"])

    def test_item_que_saiu_da_conexao_nao_derruba(self):
        """O item velho saiu do Item IDs: o que estava guardado dele sai junto.

        Parado ha 5 dias, ele derrubaria a conexao para sempre, porque nunca
        mais e pedido a Pluggy e nunca mais atualiza.
        """
        self.conexao.conectada_em = AGORA
        self.conexao.item_ids = "item-novo"
        self.conexao.itens = {
            "item-velho": {"status": "UPDATED", "atualizado_em": "2026-10-13T15:00:00.000Z"},
        }
        self.conexao.save()

        self.rodar(ClienteFalso())

        self.assertEqual(self.conexao.estado, ConexaoPluggy.Estado.ATIVA)
        self.assertEqual(list(self.conexao.itens), ["item-novo"])
        self.assertEqual(len(mail.outbox), 0)

    def test_erro_inesperado_conta_como_falha(self):
        with self.assertLogs("app.services.pluggy.busca", level="ERROR"):
            self.rodar(ClienteQueQuebraNoExtrato(KeyError("date")))

        self.assertEqual(self.conexao.falhas_seguidas, 1)
        self.assertEqual(
            self.conexao.ultimo_erro,
            "Erro inesperado na busca. Veja o log do serviço pluggy.",
        )
        self.assertEqual(TransacaoBancaria.objects.count(), 0)

    def test_buscar_todas_segue_depois_de_uma_conexao_que_explode(self):
        outra = ConexaoPluggy.objects.create(
            conta=self.titular, email="o@b.c", client_id="cid-outra",
            client_secret_cifrado=cifrar("segredo"), item_ids="item2",
            estado=ConexaoPluggy.Estado.ATIVA,
        )

        def cliente_da_conexao(client_id, segredo):
            if client_id == self.conexao.client_id:
                return ClienteQueQuebraNoExtrato(KeyError("date"))
            return ClienteFalso()

        with mock.patch.object(busca, "ClientePluggy", side_effect=cliente_da_conexao):
            with self.assertLogs("app.services.pluggy.busca", level="ERROR"):
                resultados = busca.buscar_todas(agora=AGORA)

        self.assertEqual(len(resultados), 2)
        self.conexao.refresh_from_db()
        outra.refresh_from_db()
        self.assertEqual(self.conexao.falhas_seguidas, 1)
        self.assertEqual(outra.falhas_seguidas, 0)
        self.assertEqual(outra.ultima_busca_ok_em, AGORA)

    def _cair_por_autorizacao(self):
        self.conexao.conectada_em = AGORA
        self.conexao.save()
        self.rodar(ClienteFalso(status="LOGIN_ERROR"))
        self.assertEqual(self.conexao.estado, ConexaoPluggy.Estado.COM_ERRO)
        mail.outbox.clear()

    def test_rodada_que_falhou_nao_avisa_que_voltou(self):
        """O item ja esta UPDATED, mas o extrato deu 404: nao voltou."""
        self._cair_por_autorizacao()

        self.rodar(
            ClienteQueQuebraNoExtrato(
                FalhaDaPluggy(404, "A Pluggy não achou o item ou a conta (Item ID errado?).")
            )
        )

        self.assertEqual(self.conexao.estado, ConexaoPluggy.Estado.COM_ERRO)
        self.assertEqual(mail.outbox, [])

    def test_falha_solta_nao_apaga_o_motivo_da_queda(self):
        self._cair_por_autorizacao()

        self.rodar(
            ClienteQueQuebraNoExtrato(
                FalhaDaPluggy(None, "A Pluggy não respondeu."), status="LOGIN_ERROR"
            )
        )

        self.assertEqual(self.conexao.estado, ConexaoPluggy.Estado.COM_ERRO)
        self.assertEqual(self.conexao.ultimo_erro, busca.MOTIVO_AUTORIZACAO)

    def test_busca_nao_religa_conexao_desligada_no_meio_do_caminho(self):
        """A busca leu a conexao ativa; antes de rodar, o dono a desligou."""
        lida_antes = ConexaoPluggy.objects.get(pk=self.conexao.pk)
        ConexaoPluggy.objects.filter(pk=self.conexao.pk).update(
            estado=ConexaoPluggy.Estado.DESLIGADA
        )
        cliente = ClienteFalso(transacoes=[transacao("t1")])

        resultado = busca.buscar_conexao(lida_antes, agora=AGORA, cliente=cliente)

        self.conexao.refresh_from_db()
        self.assertEqual(self.conexao.estado, ConexaoPluggy.Estado.DESLIGADA)
        self.assertEqual(resultado.estado_depois, ConexaoPluggy.Estado.DESLIGADA)
        self.assertEqual(cliente.pedidos, [])
        self.assertEqual(TransacaoBancaria.objects.count(), 0)
        self.assertIsNone(self.conexao.ultima_busca_ok_em)
        self.assertEqual(mail.outbox, [])

    def test_busca_nao_desfaz_o_que_o_dono_mudou_durante_ela(self):
        pk = self.conexao.pk

        class ClienteComMudancaNoMeio(ClienteFalso):
            def transacoes(self, account_id, de, ate):
                ConexaoPluggy.objects.filter(pk=pk).update(
                    email="novo@b.c", item_ids="item1,item2"
                )
                return super().transacoes(account_id, de, ate)

        self.rodar(ClienteComMudancaNoMeio())

        self.assertEqual(self.conexao.email, "novo@b.c")
        self.assertEqual(self.conexao.item_ids, "item1,item2")
        self.assertEqual(self.conexao.ultima_busca_ok_em, AGORA)
