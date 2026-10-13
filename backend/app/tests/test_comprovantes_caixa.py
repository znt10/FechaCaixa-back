import email
import hashlib
from email import policy
from email.message import EmailMessage
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from app.models import Comprovante, LeituraDaCaixa, RemetenteDeComprovante
from app.services.comprovantes.caixa import ler_caixa
from app.services.comprovantes.leitura import CAMPOS

from .fabricas import conta_padrao

PDF = b"%PDF-1.4 " + b"x" * 100
FOTO_GRANDE = b"\x89PNG" + b"x" * (20 * 1024)
LOGO = b"\x89PNG" + b"x" * 500


def leitor(conteudo, tipo):
    # Cada arquivo com um ID proprio, para nao parecer repetido.
    dados = {campo: None for campo in CAMPOS}
    dados.update(
        e_comprovante=True,
        valor="10.00",
        data_hora="2026-10-13T10:00:00",
        id_transacao="E" + hashlib.md5(conteudo).hexdigest()[:20],
        recebedor_nome="Maria da Silva",
    )
    return dados


VERIFICADO = (
    "mx.google.com; dkim=pass header.i=@gmail.com header.s=20230601; "
    "spf=pass smtp.mailfrom=sabrina.lima@gmail.com; dmarc=pass (p=NONE) header.from=gmail.com"
)


def mensagem_de(remetente, anexos=(), message_id="<m1@teste>", autenticacao=VERIFICADO):
    mensagem = EmailMessage()
    if autenticacao:
        mensagem["Authentication-Results"] = autenticacao
    mensagem["From"] = remetente
    mensagem["To"] = "caixa@teste.local"
    mensagem["Subject"] = "comprovante"
    mensagem["Message-ID"] = message_id
    mensagem.set_content("segue")
    for nome, tipo, conteudo in anexos:
        principal, sub = tipo.split("/")
        mensagem.add_attachment(conteudo, maintype=principal, subtype=sub, filename=nome)
    return mensagem.as_bytes()


class CaixaFalsa:
    def __init__(self, emails, validade=7, proximo=None):
        self.emails = dict(emails)  # uid -> bytes
        self.validade = validade
        self.proximo = proximo or (max(self.emails, default=0) + 1)

    def estado(self):
        return self.validade, self.proximo

    def uids_depois_de(self, uid):
        return sorted(u for u in self.emails if u > uid)

    def baixar(self, uid):
        return self.emails[uid]

    def fechar(self):
        pass


@mock.patch("app.services.comprovantes.registrar.preparar_arquivo", lambda c, t: (c, t))
class LerCaixaTests(TestCase):
    def setUp(self):
        self.conta = conta_padrao()
        self.conta.modulo_comprovantes_ativo = True
        self.conta.save()
        RemetenteDeComprovante.objects.create(conta=self.conta, email="sabrina.lima@gmail.com")
        # A caixa ja foi lida ate o UID 10.
        LeituraDaCaixa.objects.create(pk=1, uid_validade=7, ultimo_uid=10)

    def test_primeira_vez_comeca_do_fim_e_nao_importa_nada(self):
        LeituraDaCaixa.objects.all().delete()
        caixa = CaixaFalsa({5: mensagem_de("sabrina.lima@gmail.com", [("a.pdf", "application/pdf", PDF)])})

        ler_caixa(caixa, ler=leitor)

        self.assertFalse(Comprovante.objects.exists())
        self.assertEqual(LeituraDaCaixa.objects.get(pk=1).ultimo_uid, 5)

    def test_cinco_anexos_viram_cinco_comprovantes(self):
        anexos = [(f"c{i}.pdf", "application/pdf", PDF + bytes([i])) for i in range(5)]
        resumo = ler_caixa(CaixaFalsa({11: mensagem_de("sabrina.lima@gmail.com", anexos)}), ler=leitor)
        self.assertEqual(resumo.comprovantes, 5)
        self.assertEqual(Comprovante.objects.filter(conta=self.conta).count(), 5)
        self.assertEqual(LeituraDaCaixa.objects.get(pk=1).ultimo_uid, 11)

    def test_remetente_com_nome_e_maiusculas_e_reconhecido(self):
        """Foco da revisao 1."""
        caixa = CaixaFalsa({
            11: mensagem_de("Sabrina Lima <Sabrina.Lima@Gmail.com>", [("a.pdf", "application/pdf", PDF)])
        })
        ler_caixa(caixa, ler=leitor)
        self.assertEqual(Comprovante.objects.get().remetente, "sabrina.lima@gmail.com")

    def test_remetente_desconhecido_e_ignorado(self):
        caixa = CaixaFalsa({11: mensagem_de("estranho@gmail.com", [("a.pdf", "application/pdf", PDF)])})
        resumo = ler_caixa(caixa, ler=leitor)
        self.assertEqual(resumo.ignorados, 1)
        self.assertFalse(Comprovante.objects.exists())

    def test_empresa_com_modulo_desligado_e_ignorada(self):
        self.conta.modulo_comprovantes_ativo = False
        self.conta.save()
        caixa = CaixaFalsa({11: mensagem_de("sabrina.lima@gmail.com", [("a.pdf", "application/pdf", PDF)])})
        ler_caixa(caixa, ler=leitor)
        self.assertFalse(Comprovante.objects.exists())

    def test_logo_pequena_e_ignorada_e_foto_conta(self):
        anexos = [("logo.png", "image/png", LOGO), ("foto.png", "image/png", FOTO_GRANDE)]
        ler_caixa(CaixaFalsa({11: mensagem_de("sabrina.lima@gmail.com", anexos)}), ler=leitor)
        self.assertEqual(Comprovante.objects.get().arquivo_nome, "foto.png")

    def test_tipo_nao_aceito_e_ignorado(self):
        anexos = [("convite.ics", "text/calendar", b"BEGIN:VCALENDAR")]
        ler_caixa(CaixaFalsa({11: mensagem_de("sabrina.lima@gmail.com", anexos)}), ler=leitor)
        self.assertFalse(Comprovante.objects.exists())

    def test_email_encaminhado_como_anexo_entra(self):
        """Foco da revisao 3: o comprovante dentro de um .eml anexado."""
        interno = email.message_from_bytes(
            mensagem_de("banco@nubank.com.br", [("c.pdf", "application/pdf", PDF)], "<interno>"),
            policy=policy.default,
        )
        externo = EmailMessage()
        externo["From"] = "sabrina.lima@gmail.com"
        externo["Message-ID"] = "<externo>"
        externo.set_content("encaminhando")
        externo.add_attachment(interno)  # vira message/rfc822

        ler_caixa(CaixaFalsa({11: externo.as_bytes()}), ler=leitor)

        self.assertEqual(Comprovante.objects.count(), 1)

    def test_queda_no_meio_nao_duplica_e_nao_le_de_novo(self):
        mensagem = mensagem_de("sabrina.lima@gmail.com", [("a.pdf", "application/pdf", PDF)])
        ler_caixa(CaixaFalsa({11: mensagem}), ler=leitor)
        # Simula a queda: o UID nao avancou, a mesma mensagem volta.
        LeituraDaCaixa.objects.filter(pk=1).update(ultimo_uid=10)
        leituras = []

        def contar(conteudo, tipo):
            leituras.append(1)
            return leitor(conteudo, tipo)

        ler_caixa(CaixaFalsa({11: mensagem}), ler=contar)

        self.assertEqual(Comprovante.objects.count(), 1)
        self.assertEqual(leituras, [])

    def test_email_ja_aberto_no_gmail_entra_igual(self):
        """Nao ha "nao lido": so o UID decide. A CaixaFalsa nem sabe de \\Seen."""
        caixa = CaixaFalsa({11: mensagem_de("sabrina.lima@gmail.com", [("a.pdf", "application/pdf", PDF)])})
        ler_caixa(caixa, ler=leitor)
        self.assertEqual(Comprovante.objects.count(), 1)

    def test_troca_de_uidvalidity_recomeca_do_fim(self):
        caixa = CaixaFalsa(
            {3: mensagem_de("sabrina.lima@gmail.com", [("a.pdf", "application/pdf", PDF)])},
            validade=99,
        )
        ler_caixa(caixa, ler=leitor)
        estado = LeituraDaCaixa.objects.get(pk=1)
        self.assertEqual((estado.uid_validade, estado.ultimo_uid), (99, 3))
        self.assertFalse(Comprovante.objects.exists())


    def test_um_email_com_erro_nao_trava_os_seguintes(self):
        """Revisao final: um e-mail ruim prendia a caixa de todas as empresas."""
        ruim = mensagem_de("sabrina.lima@gmail.com", [("a.pdf", "application/pdf", PDF)], "<ruim>")
        bom = mensagem_de("sabrina.lima@gmail.com", [("b.pdf", "application/pdf", PDF + b"b")], "<bom>")

        def explode_no_primeiro(conteudo, tipo):
            if conteudo == PDF:
                raise RuntimeError("inesperado")
            return leitor(conteudo, tipo)

        resumo = ler_caixa(CaixaFalsa({11: ruim, 12: bom}), ler=explode_no_primeiro)

        self.assertEqual(Comprovante.objects.get().arquivo_nome, "b.pdf")
        self.assertEqual(resumo.falhas, 1)
        self.assertEqual(LeituraDaCaixa.objects.get(pk=1).ultimo_uid, 12)

    def test_remetente_nao_verificado_entra_para_conferir(self):
        """Revisao final: From falsificado nao pode somar sem alguem olhar."""
        caixa = CaixaFalsa({
            11: mensagem_de("sabrina.lima@gmail.com", [("a.pdf", "application/pdf", PDF)], autenticacao=None)
        })
        ler_caixa(caixa, ler=leitor)
        comprovante = Comprovante.objects.get()
        self.assertEqual(comprovante.situacao, Comprovante.Situacao.CONFERIR)
        self.assertIn("não verificado", comprovante.motivo)

    def test_autenticacao_falsa_de_outro_servidor_nao_vale(self):
        falsa = "servidor.qualquer; dmarc=pass header.from=gmail.com"
        caixa = CaixaFalsa({
            11: mensagem_de("sabrina.lima@gmail.com", [("a.pdf", "application/pdf", PDF)], autenticacao=falsa)
        })
        ler_caixa(caixa, ler=leitor)
        self.assertEqual(Comprovante.objects.get().situacao, Comprovante.Situacao.CONFERIR)

    def test_remetente_verificado_entra_ok(self):
        caixa = CaixaFalsa({11: mensagem_de("sabrina.lima@gmail.com", [("a.pdf", "application/pdf", PDF)])})
        ler_caixa(caixa, ler=leitor)
        self.assertEqual(Comprovante.objects.get().situacao, Comprovante.Situacao.OK)

    def test_pdf_anexado_como_octet_stream_entra_como_pdf(self):
        """Revisao final: Outlook e apps de banco anexam PDF com tipo generico."""
        anexos = [("comprovante.pdf", "application/octet-stream", PDF)]
        ler_caixa(CaixaFalsa({11: mensagem_de("sabrina.lima@gmail.com", anexos)}), ler=leitor)
        self.assertEqual(Comprovante.objects.get().arquivo_tipo, "application/pdf")

    def test_octet_stream_que_nao_e_pdf_nem_imagem_e_ignorado(self):
        anexos = [("dados.bin", "application/octet-stream", b"\x00" * 100)]
        ler_caixa(CaixaFalsa({11: mensagem_de("sabrina.lima@gmail.com", anexos)}), ler=leitor)
        self.assertFalse(Comprovante.objects.exists())

class ComandoTests(TestCase):
    @override_settings(COMPROVANTES_EMAIL="", COMPROVANTES_SENHA_APP="")
    def test_sem_configuracao_avisa_e_sai(self):
        saida = StringIO()
        call_command("ler_caixa_de_comprovantes", stdout=saida)
        self.assertIn("não configurada", saida.getvalue())

    @override_settings(COMPROVANTES_EMAIL="caixa@teste.local", COMPROVANTES_SENHA_APP="x")
    def test_uma_volta_usa_a_caixa_e_fecha(self):
        caixa = CaixaFalsa({})
        with mock.patch(
            "app.management.commands.ler_caixa_de_comprovantes.CaixaImap",
            return_value=caixa,
        ) as fabrica, mock.patch.object(caixa, "fechar") as fechar:
            call_command("ler_caixa_de_comprovantes", stdout=StringIO())
        fabrica.assert_called_once_with("caixa@teste.local", "x")
        fechar.assert_called_once()

    @override_settings(COMPROVANTES_EMAIL="caixa@teste.local", COMPROVANTES_SENHA_APP="x")
    def test_falha_de_conexao_nao_derruba_o_comando(self):
        with mock.patch(
            "app.management.commands.ler_caixa_de_comprovantes.CaixaImap",
            side_effect=OSError("sem rede"),
        ):
            call_command("ler_caixa_de_comprovantes", stdout=StringIO())
