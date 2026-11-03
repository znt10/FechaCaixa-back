"""Os boletos no painel da gerencia: a lista, o aviso, pagar e o arquivo."""

import io
import os
from datetime import date
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APITestCase

from app.models import Boleto, Conta

from .fabricas import vincular_conta

URL = "/api/v1/boletos/"


def usuario(conta, grupo, nome):
    user = User.objects.create_user(username=f"{nome}@aurora.com", password="x", first_name=nome)
    user.groups.add(Group.objects.get_or_create(name=grupo)[0])
    vincular_conta(user, conta)
    return user


@mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""})
class BoletosDoPainelTests(APITestCase):
    def setUp(self):
        self.conta = Conta.objects.create(nome="Aurora", boletos_ativos=True)
        self.gerente = usuario(self.conta, "Gerente", "Ana")
        self.client.force_authenticate(user=self.gerente)

    def boleto(self, **campos):
        return Boleto.objects.create(conta=self.conta, origem="FORMULARIO", **campos)

    def test_lista_sem_data_primeiro_e_depois_por_vencimento(self):
        depois = self.boleto(vencimento=date(2026, 11, 20))
        sem_data = self.boleto()
        antes = self.boleto(vencimento=date(2026, 11, 5))

        resp = self.client.get(URL)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            [b["id"] for b in resp.data],
            [str(sem_data.public_id), str(antes.public_id), str(depois.public_id)],
        )

    def test_pagos_em_outra_aba(self):
        self.boleto(vencimento=date(2026, 11, 5))
        pago = self.boleto(vencimento=date(2026, 11, 1), situacao=Boleto.Situacao.PAGO)

        a_pagar = self.client.get(URL).data
        pagos = self.client.get(URL, {"situacao": "PAGO"}).data

        self.assertNotIn(str(pago.public_id), [b["id"] for b in a_pagar])
        self.assertEqual([b["id"] for b in pagos], [str(pago.public_id)])

    def test_descartado_some(self):
        self.boleto(situacao=Boleto.Situacao.DESCARTADO)

        self.assertEqual(self.client.get(URL).data, [])

    def test_outra_empresa_nao_ve(self):
        outra = Conta.objects.create(nome="Rival", boletos_ativos=True)
        alheio = Boleto.objects.create(conta=outra, origem="FORMULARIO")

        self.assertEqual(self.client.get(f"{URL}{alheio.public_id}/").status_code, 404)
        self.assertEqual(self.client.get(URL).data, [])

    def test_funcionario_e_403(self):
        self.client.force_authenticate(user=usuario(self.conta, "Funcionario", "Bia"))

        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_empresa_sem_boletos_e_403(self):
        self.conta.boletos_ativos = False
        self.conta.save()

        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_pagar_e_desfazer(self):
        boleto = self.boleto(vencimento=date(2026, 11, 5))

        pago = self.client.post(f"{URL}{boleto.public_id}/pagar/")
        self.assertEqual(pago.status_code, 200)
        self.assertEqual(pago.data["situacao"], "PAGO")
        self.assertEqual(pago.data["pago_por_nome"], "Ana")
        self.assertIsNotNone(pago.data["pago_em"])

        desfeito = self.client.post(f"{URL}{boleto.public_id}/desfazer_pagamento/")
        self.assertEqual(desfeito.data["situacao"], "A_PAGAR")
        self.assertIsNone(desfeito.data["pago_em"])
        self.assertIsNone(desfeito.data["pago_por_nome"])

    def test_descartar(self):
        boleto = self.boleto()

        resp = self.client.post(f"{URL}{boleto.public_id}/descartar/")

        self.assertEqual(resp.data["situacao"], "DESCARTADO")

    def test_editar_tira_o_conferir(self):
        boleto = self.boleto(conferir=True)

        resp = self.client.patch(
            f"{URL}{boleto.public_id}/", {"vencimento": "2026-11-20"}, format="json"
        )

        self.assertEqual(resp.status_code, 200)
        boleto.refresh_from_db()
        self.assertEqual(boleto.vencimento, date(2026, 11, 20))
        self.assertIs(boleto.conferir, False)
        self.assertIn("vencimento", boleto.campos_editados)

    def test_editar_com_loja_de_outra_empresa_e_400(self):
        from .fabricas import criar_loja

        outra = Conta.objects.create(nome="Rival")
        loja_rival = criar_loja(nome_loja="Rival", cidade="Patos", endereco="Rua 9", conta=outra)
        boleto = self.boleto()

        resp = self.client.patch(
            f"{URL}{boleto.public_id}/", {"loja": str(loja_rival.public_id)}, format="json"
        )

        self.assertEqual(resp.status_code, 400)

    def test_lista_nao_pagina(self):
        for _ in range(60):
            self.boleto()

        resp = self.client.get(URL)

        self.assertIsInstance(resp.data, list)
        self.assertEqual(len(resp.data), 60)

    def test_arquivo_devolve_os_bytes(self):
        boleto = self.boleto(arquivo=b"%PDF-1.4 x", arquivo_tipo="application/pdf",
                             arquivo_nome="boleto.pdf")

        resp = self.client.get(f"{URL}{boleto.public_id}/arquivo/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"%PDF-1.4 x")
        self.assertEqual(resp["Content-Type"], "application/pdf")

    def test_resumo(self):
        self.boleto()

        resp = self.client.get(f"{URL}resumo/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["sem_data"], 1)
        self.assertEqual(resp.data["aviso"], 1)

    def test_envio_pelo_painel_sem_loja(self):
        saida = io.BytesIO()
        Image.new("RGB", (50, 50)).save(saida, format="JPEG")
        foto = SimpleUploadedFile("conta.jpg", saida.getvalue(), content_type="image/jpeg")

        resp = self.client.post(URL, {"arquivo": foto, "vencimento": "2026-11-25"},
                                format="multipart")

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["origem"], "PAINEL")
        self.assertIsNone(resp.data["loja"])
        self.assertEqual(Boleto.objects.get().enviado_por, self.gerente)
