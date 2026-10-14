import os
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from app.models import Comprovante, Conta, RemetenteDeComprovante
from app.services.comprovantes.leitura import CAMPOS

from .fabricas import criar_loja, lista
from .test_comprovantes_api import BaseApi


def leitura_falsa(conteudo, tipo):
    dados = {campo: None for campo in CAMPOS}
    dados.update(
        e_comprovante=True,
        valor="50.00",
        data_hora="2026-10-13T09:00:00",
        id_transacao="E00000000202610130000aaaa0009",
        recebedor_nome="Loterica Central",
    )
    return dados


# Na view, e nao no registrar: o default `ler=ler_arquivo` do registrar ja
# esta ligado na definicao da funcao, e a view passa o dela explicitamente.
@mock.patch("app.api.v1.views.comprovantes.ler_arquivo", leitura_falsa)
class EnviarPeloSiteTests(BaseApi):
    def enviar(self, nome="c.pdf", tipo="application/pdf", conteudo=b"%PDF-1.4", **extra):
        return self.api.post(
            "/api/v1/comprovantes/enviar/",
            {"arquivo": SimpleUploadedFile(nome, conteudo, content_type=tipo), **extra},
            format="multipart",
        )

    def test_envio_lido_por_inteiro_volta_ok(self):
        resposta = self.enviar()
        self.assertEqual(resposta.status_code, 201, resposta.data)
        self.assertEqual(resposta.data["situacao"], "OK")
        self.assertEqual(resposta.data["valor"], "50.00")
        comprovante = Comprovante.objects.get()
        self.assertEqual(comprovante.origem, "SITE")
        self.assertEqual(comprovante.enviado_por, self.user)

    def test_arquivo_que_nao_e_comprovante_e_recusado(self):
        def curriculo(conteudo, tipo):
            dados = leitura_falsa(conteudo, tipo)
            dados["e_comprovante"] = False
            return dados

        with mock.patch("app.api.v1.views.comprovantes.ler_arquivo", curriculo):
            resposta = self.enviar(nome="curriculo.pdf")
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("não parece um comprovante", resposta.json()["error"])
        self.assertFalse(Comprovante.objects.exists())

    def test_loja_escolhida_vai_junto(self):
        resposta = self.enviar(loja=str(self.loja.public_id))
        # Pelo JSON, que e o que o front le: em `resposta.data` o id e um UUID.
        self.assertEqual(resposta.json()["loja"], str(self.loja.public_id))

    def test_loja_de_outra_empresa_e_400(self):
        outra = Conta.objects.create(nome="Outra")
        alheia = criar_loja(conta=outra, nome_loja="X", cidade="X", endereco="X")
        self.assertEqual(self.enviar(loja=str(alheia.public_id)).status_code, 400)

    def test_heic_e_400_com_frase_clara(self):
        """Foco da revisao 4."""
        resposta = self.enviar(nome="IMG_0001.HEIC", tipo="image/heic", conteudo=b"heic")
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("PDF, PNG, JPG ou WEBP", str(resposta.data))

    def test_sem_arquivo_e_400(self):
        resposta = self.api.post("/api/v1/comprovantes/enviar/", {}, format="multipart")
        self.assertEqual(resposta.status_code, 400)

    def test_repetido_e_409(self):
        self.enviar()
        self.assertEqual(self.enviar().status_code, 409)


class ConfiguracaoTests(BaseApi):
    @override_settings(COMPROVANTES_EMAIL="caixa@teste.local")
    @mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"})
    def test_mostra_o_endereco_e_se_a_leitura_esta_ligada(self):
        dados = self.api.get("/api/v1/comprovantes/configuracao/").data
        self.assertEqual(dados, {"endereco": "caixa@teste.local", "leitura_ligada": True})


class RemetentesTests(BaseApi):
    URL = "/api/v1/remetentes-de-comprovante/"

    def test_cadastra_em_minusculas_e_lista_so_os_da_empresa(self):
        outra = Conta.objects.create(nome="Outra")
        RemetenteDeComprovante.objects.create(conta=outra, email="dela@x.com")
        resposta = self.api.post(self.URL, {"email": "Sabrina@Gmail.com"}, format="json")
        self.assertEqual(resposta.status_code, 201, resposta.data)
        self.assertEqual([r["email"] for r in lista(self.api.get(self.URL))], ["sabrina@gmail.com"])

    def test_email_de_outra_empresa_e_400_sem_dizer_qual(self):
        outra = Conta.objects.create(nome="Padaria do Vizinho")
        RemetenteDeComprovante.objects.create(conta=outra, email="dela@x.com")
        resposta = self.api.post(self.URL, {"email": "DELA@x.com"}, format="json")
        self.assertEqual(resposta.status_code, 400)
        self.assertNotIn("Vizinho", str(resposta.data))

    def test_apaga_so_o_da_propria_empresa(self):
        outra = Conta.objects.create(nome="Outra")
        alheio = RemetenteDeComprovante.objects.create(conta=outra, email="dela@x.com")
        self.assertEqual(self.api.delete(f"{self.URL}{alheio.public_id}/").status_code, 404)
        meu = RemetenteDeComprovante.objects.create(conta=self.conta, email="meu@x.com")
        self.assertEqual(self.api.delete(f"{self.URL}{meu.public_id}/").status_code, 204)
