from django.test import TestCase

from app.models import (
    Comprovante,
    Conta,
    ContaBancaria,
    ElementoDeDespesa,
    RegraDeCategoriaDoRecebedor,
)
from app.services.comprovantes.categoria import aprender_categoria, categoria_sugerida
from app.services.comprovantes.loja import achar_loja
from app.services.plano_de_contas import garantir_plano_de_contas

from .fabricas import conta_padrao, criar_loja


class AcharLojaTests(TestCase):
    def setUp(self):
        self.conta = conta_padrao()
        self.loja_a = criar_loja(
            nome_loja="Loja A", cidade="Patos", endereco="Rua 1", cnpj="11222333000181"
        )
        self.loja_b = criar_loja(nome_loja="Loja B", cidade="Patos", endereco="Rua 2")
        ContaBancaria.objects.create(
            loja=self.loja_b, banco="SANTANDER", agencia="3991", numero="13005228-1"
        )

    def test_pelo_cnpj_completo(self):
        self.assertEqual(achar_loja(self.conta, "11.222.333/0001-81"), self.loja_a)

    def test_cnpj_mascarado_nao_acha(self):
        self.assertIsNone(achar_loja(self.conta, "11.******/****-81"))

    def test_pela_agencia_e_conta(self):
        self.assertEqual(
            achar_loja(self.conta, "", agencia="03991", numero="130052281"), self.loja_b
        )

    def test_conta_bancaria_desativada_nao_conta(self):
        ContaBancaria.objects.update(ativo=False)
        self.assertIsNone(achar_loja(self.conta, "", agencia="3991", numero="13005228-1"))

    def test_nada_bate_e_sem_loja(self):
        self.assertIsNone(achar_loja(self.conta, "99888777000166"))

    def test_loja_de_outra_empresa_nao_e_achada(self):
        outra = Conta.objects.create(nome="Outra empresa")
        criar_loja(
            conta=outra, nome_loja="Dela", cidade="X", endereco="Y", cnpj="99888777000166"
        )
        self.assertIsNone(achar_loja(self.conta, "99888777000166"))


class CategoriaTests(TestCase):
    def setUp(self):
        self.conta = conta_padrao()
        garantir_plano_de_contas(self.conta)
        self.aluguel = ElementoDeDespesa.objects.get(grupo__conta=self.conta, nome="Aluguel")
        self.energia = ElementoDeDespesa.objects.get(grupo__conta=self.conta, nome="Energia")

    def _comprovante(self, elemento):
        return Comprovante(
            conta=self.conta,
            origem="EMAIL",
            recebedor_normalizado="MARIA DA SILVA",
            elemento=elemento,
        )

    def test_sem_regra_nao_sugere(self):
        self.assertIsNone(categoria_sugerida(self.conta, "MARIA DA SILVA"))

    def test_aprende_e_sugere(self):
        aprender_categoria(self._comprovante(self.aluguel))
        self.assertEqual(categoria_sugerida(self.conta, "MARIA DA SILVA"), self.aluguel)

    def test_trocar_a_categoria_troca_a_regra(self):
        aprender_categoria(self._comprovante(self.aluguel))
        aprender_categoria(self._comprovante(self.energia))
        self.assertEqual(categoria_sugerida(self.conta, "MARIA DA SILVA"), self.energia)
        self.assertEqual(RegraDeCategoriaDoRecebedor.objects.count(), 1)

    def test_sem_categoria_nao_aprende(self):
        aprender_categoria(self._comprovante(None))
        self.assertFalse(RegraDeCategoriaDoRecebedor.objects.exists())

    def test_regra_de_outra_empresa_nao_vale(self):
        aprender_categoria(self._comprovante(self.aluguel))
        outra = Conta.objects.create(nome="Outra empresa")
        self.assertIsNone(categoria_sugerida(outra, "MARIA DA SILVA"))
