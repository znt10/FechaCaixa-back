"""O boleto enviado pelo aparelho da loja, no fechamento do caixa.

O aparelho nao tem login: entra pelo codigo da empresa, como no lancamento do
caixa. A leitura fica desligada aqui (sem chave), e o boleto e guardado como
a loja mandou.
"""

import io
import os
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APITestCase

from app.models import Boleto, Conta

from .fabricas import criar_loja

URL = "/api/v1/formulario/boletos/"


def jpeg():
    saida = io.BytesIO()
    Image.new("RGB", (50, 50), (200, 200, 200)).save(saida, format="JPEG")
    return SimpleUploadedFile("boleto.jpg", saida.getvalue(), content_type="image/jpeg")


@mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""})
class BoletoDoFormularioTests(APITestCase):
    def setUp(self):
        # O acesso ao formulario tem limite por IP, guardado no cache: sem
        # limpar, os testes de cima gastam a cota dos de baixo.
        cache.clear()
        self.conta = Conta.objects.create(nome="Aurora", boletos_ativos=True)
        self.loja = criar_loja(
            nome_loja="Centro", cidade="Joao Pessoa", endereco="Rua 1", conta=self.conta
        )

    def entrar(self, conta=None):
        conta = conta or self.conta
        self.client.post(
            "/api/v1/formulario/acesso/",
            {"codigo": conta.codigo_acesso, "apelido": "Celular"},
            format="json",
        )

    def enviar(self, **campos):
        dados = {"arquivo": jpeg(), "loja": str(self.loja.public_id), **campos}
        return self.client.post(URL, dados, format="multipart")

    def test_aparelho_salva_boleto_com_vencimento(self):
        self.entrar()

        resp = self.enviar(vencimento="2026-11-20")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["vencimento"], "2026-11-20")
        self.assertEqual(resp.data["origem"], "FORMULARIO")
        boleto = Boleto.objects.get()
        self.assertEqual(boleto.loja, self.loja)
        self.assertIsNotNone(boleto.dispositivo)

    def test_sem_vencimento_salva_sem_data(self):
        self.entrar()

        resp = self.enviar()

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(resp.data["vencimento"])

    def test_sem_aparelho_e_401(self):
        self.assertEqual(self.enviar().status_code, 401)

    def test_empresa_sem_boletos_e_403(self):
        self.conta.boletos_ativos = False
        self.conta.save()
        self.entrar()

        resp = self.enviar()

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Boleto.objects.count(), 0)

    def test_loja_de_outra_empresa_e_400_e_nao_grava(self):
        outra = Conta.objects.create(nome="Rival")
        loja_rival = criar_loja(nome_loja="Rival", cidade="Patos", endereco="Rua 9", conta=outra)
        self.entrar()

        resp = self.enviar(loja=str(loja_rival.public_id))

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Boleto.objects.count(), 0)

    def test_loja_desativada_e_400(self):
        self.loja.ativo = False
        self.loja.save()
        self.entrar()

        self.assertEqual(self.enviar().status_code, 400)
        self.assertEqual(Boleto.objects.count(), 0)

    def test_heic_e_400_com_frase(self):
        self.entrar()
        heic = SimpleUploadedFile("IMG_0001.heic", b"\x00" * 100, content_type="image/heic")

        resp = self.enviar(arquivo=heic)

        self.assertEqual(resp.status_code, 400)
        self.assertIn("PDF, PNG, JPG ou WEBP", resp.data["error"])

    def test_sem_arquivo_e_400(self):
        self.entrar()

        resp = self.client.post(URL, {"loja": str(self.loja.public_id)}, format="multipart")

        self.assertEqual(resp.status_code, 400)

    def test_valor_brasileiro(self):
        self.entrar()

        resp = self.enviar(valor="1.234,56")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["valor"], "1234.56")

    def test_valor_zero_e_nao_digitado(self):
        """O campo de moeda apagado mostra 0,00: recusar travaria a loja."""
        self.entrar()

        resp = self.enviar(valor="0.00")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertIsNone(resp.data["valor"])

    def test_mesma_foto_duas_vezes_e_409(self):
        self.entrar()
        foto = jpeg().read()

        primeiro = self.client.post(
            URL,
            {"arquivo": SimpleUploadedFile("a.jpg", foto, content_type="image/jpeg"),
             "loja": str(self.loja.public_id)},
            format="multipart",
        )
        segundo = self.client.post(
            URL,
            {"arquivo": SimpleUploadedFile("b.jpg", foto, content_type="image/jpeg"),
             "loja": str(self.loja.public_id)},
            format="multipart",
        )

        self.assertEqual(primeiro.status_code, 201, primeiro.data)
        self.assertEqual(segundo.status_code, 409)
        self.assertEqual(segundo.data["error"], "Este boleto já foi lançado.")
        self.assertEqual(Boleto.objects.count(), 1)
