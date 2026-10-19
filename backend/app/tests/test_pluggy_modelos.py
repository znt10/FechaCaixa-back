import datetime
from decimal import Decimal

from cryptography.fernet import Fernet
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings

from app.models import ConexaoPluggy, ContaBancaria, TransacaoBancaria
from app.services.extratos.linhas import LinhaDoExtrato

from .fabricas import conta_padrao, criar_loja

# Os campos que a 0059 trouxe. O codigo de antes dela (um rollback, ou o app de
# outra pasta no mesmo banco) nao os conhece e nao os manda no INSERT.
CAMPOS_DA_0059 = {
    "id_externo",
    "chave_do_arquivo",
    "contraparte_nome",
    "contraparte_documento",
    "contraparte_conta",
}


class PluggyModelosTests(TestCase):
    def setUp(self):
        self.conta = conta_padrao()
        self.loja = criar_loja(nome_loja="Centro")
        self.conta_bancaria = ContaBancaria.objects.create(
            loja=self.loja, banco="SANTANDER", agencia="3301", numero="12345-6"
        )

    def _transacao(self, chave, **campos):
        return TransacaoBancaria.objects.create(
            conta=self.conta,
            conta_bancaria=self.conta_bancaria,
            data=datetime.date(2026, 10, 1),
            valor=Decimal("10.00"),
            descricao="PIX",
            chave=chave,
            **campos,
        )

    def test_insert_do_codigo_de_antes_da_pluggy_passa_sem_a_contraparte(self):
        """O INSERT cru, so com as colunas que existiam antes da 0059.

        Sem DEFAULT no banco, o MySQL estrito recusa a linha e todo upload de
        OFX do codigo antigo vira 500.
        """
        antiga = TransacaoBancaria(
            conta=self.conta,
            conta_bancaria=self.conta_bancaria,
            data=datetime.date(2026, 10, 1),
            valor=Decimal("10.00"),
            descricao="PIX",
            chave="antiga",
        )
        campos = [
            campo
            for campo in TransacaoBancaria._meta.concrete_fields
            if not campo.primary_key and campo.name not in CAMPOS_DA_0059
        ]
        valores = [
            campo.get_db_prep_save(campo.pre_save(antiga, add=True), connection)
            for campo in campos
        ]
        tabela = connection.ops.quote_name(TransacaoBancaria._meta.db_table)
        colunas = ", ".join(connection.ops.quote_name(campo.column) for campo in campos)
        marcas = ", ".join(["%s"] * len(campos))
        with connection.cursor() as cursor:
            cursor.execute(f"INSERT INTO {tabela} ({colunas}) VALUES ({marcas})", valores)

        gravada = TransacaoBancaria.objects.get(chave="antiga")
        self.assertEqual(
            (
                gravada.contraparte_nome,
                gravada.contraparte_documento,
                gravada.contraparte_conta,
            ),
            ("", "", ""),
        )

    def test_dois_ids_externos_vazios_convivem(self):
        self._transacao("a")
        self._transacao("b")
        self.assertEqual(TransacaoBancaria.objects.count(), 2)

    def test_id_externo_repetido_na_mesma_conta_bancaria_e_barrado(self):
        self._transacao("a", id_externo="x1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._transacao("b", id_externo="x1")

    def test_chave_do_arquivo_repetida_na_mesma_conta_bancaria_e_barrada(self):
        self._transacao("a", chave_do_arquivo="k1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._transacao("b", chave_do_arquivo="k1")

    def test_mesmo_id_externo_em_outra_conta_bancaria_passa(self):
        outra = ContaBancaria.objects.create(
            loja=self.loja, banco="INTER", agencia="1", numero="999"
        )
        self._transacao("a", id_externo="x1")
        TransacaoBancaria.objects.create(
            conta=self.conta, conta_bancaria=outra, data=datetime.date(2026, 10, 1),
            valor=Decimal("1.00"), descricao="PIX", chave="a", id_externo="x1",
        )

    def test_pluggy_account_id_e_unico_mas_vazio_repete(self):
        ContaBancaria.objects.create(
            loja=self.loja, banco="INTER", agencia="1", numero="1", pluggy_account_id="p1"
        )
        ContaBancaria.objects.create(loja=self.loja, banco="INTER", agencia="1", numero="2")
        ContaBancaria.objects.create(loja=self.loja, banco="INTER", agencia="1", numero="3")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContaBancaria.objects.create(
                loja=self.loja, banco="INTER", agencia="1", numero="4", pluggy_account_id="p1"
            )

    def test_lista_de_itens_limpa_a_entrada(self):
        conexao = ConexaoPluggy(item_ids=" id1, id2,,id1 ,")
        self.assertEqual(conexao.lista_de_itens(), ["id1", "id2"])
        self.assertEqual(ConexaoPluggy(item_ids="").lista_de_itens(), [])

    def test_estado_comeca_configurando(self):
        conexao = ConexaoPluggy.objects.create(conta=self.conta, email="a@b.com")
        self.assertEqual(conexao.estado, ConexaoPluggy.Estado.CONFIGURANDO)
        self.assertEqual(conexao.itens, {})
        self.assertEqual(conexao.falhas_seguidas, 0)

    @override_settings(PLUGGY_CHAVE_CIFRA=Fernet.generate_key().decode())
    def test_guardar_segredo_nao_guarda_o_texto(self):
        conexao = ConexaoPluggy.objects.create(conta=self.conta, email="a@b.com")
        conexao.guardar_segredo("abc")
        self.assertNotIn("abc", conexao.client_secret_cifrado)
        self.assertIsNotNone(conexao.segredo_salvo_em)

    def test_linha_do_extrato_ganha_campos_com_default(self):
        linha = LinhaDoExtrato(
            data=datetime.date(2026, 10, 1), valor=Decimal("1"), descricao="x"
        )
        self.assertEqual(linha.id_externo, "")
        self.assertEqual(linha.contraparte_nome, "")
        self.assertEqual(linha.contraparte_documento, "")
        self.assertEqual(linha.contraparte_conta, "")
