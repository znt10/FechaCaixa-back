"""A caixa do Gmail que recebe os comprovantes.

Lida pelo UID do IMAP, nunca por "nao lidos", e sem marcar nada: o Gmail
fica como a pessoa deixar. Na primeira vez (ou se o Gmail renumerar a caixa)
comeca do fim, sem importar o que ja estava la.
"""

import email
import imaplib
import logging
import re
from dataclasses import dataclass
from email import policy
from email.utils import parseaddr

from app.models import Comprovante, LeituraDaCaixa, RemetenteDeComprovante

from .arquivo import TAMANHO_MINIMO_DE_IMAGEM, TIPOS_ACEITOS
from .leitura import ler_arquivo
from .registrar import registrar_comprovante

logger = logging.getLogger(__name__)


class CaixaImap:
    def __init__(self, endereco, senha, servidor="imap.gmail.com"):
        self.imap = imaplib.IMAP4_SSL(servidor, timeout=60)
        self.imap.login(endereco, senha)
        # readonly + BODY.PEEK: ler nao marca nada como lido.
        self.imap.select("INBOX", readonly=True)

    def estado(self):
        _, dados = self.imap.status("INBOX", "(UIDVALIDITY UIDNEXT)")
        texto = dados[0].decode()
        validade = int(re.search(r"UIDVALIDITY (\d+)", texto).group(1))
        proximo = int(re.search(r"UIDNEXT (\d+)", texto).group(1))
        return validade, proximo

    def uids_depois_de(self, uid):
        _, dados = self.imap.uid("SEARCH", None, f"UID {uid + 1}:*")
        # "N:*" sempre devolve o ultimo e-mail, mesmo com UID menor que N.
        return sorted(u for u in map(int, dados[0].split()) if u > uid)

    def baixar(self, uid):
        _, dados = self.imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
        return dados[0][1]

    def fechar(self):
        try:
            self.imap.logout()
        except Exception:
            logger.warning("Falha ao sair da caixa de comprovantes.", exc_info=True)


@dataclass
class ResumoDaLeitura:
    emails: int = 0
    comprovantes: int = 0
    ignorados: int = 0
    falhas: int = 0


def ler_caixa(caixa, ler=ler_arquivo):
    estado, _ = LeituraDaCaixa.objects.get_or_create(pk=1)
    validade, proximo = caixa.estado()

    if estado.ultimo_uid is None or estado.uid_validade != validade:
        if estado.ultimo_uid is not None:
            logger.warning("A caixa de comprovantes foi renumerada; recomecando do fim.")
        estado.uid_validade = validade
        estado.ultimo_uid = proximo - 1
        estado.save()
        return ResumoDaLeitura()

    resumo = ResumoDaLeitura()
    for uid in caixa.uids_depois_de(estado.ultimo_uid):
        # Baixar fica fora do try: rede caindo aborta a volta, e o e-mail volta
        # na proxima. O que da errado DENTRO do e-mail e problema dele.
        bruto = caixa.baixar(uid)
        resumo.emails += 1
        try:
            mensagem = email.message_from_bytes(bruto, policy=policy.default)
            criados = processar_mensagem(mensagem, uid=uid, validade=validade, ler=ler)
        except Exception:
            # A caixa e uma so: um e-mail ruim preso aqui pararia a entrada de
            # comprovantes de todas as empresas, tentando de novo a cada volta.
            logger.exception("Falha no e-mail UID %s da caixa de comprovantes; seguindo.", uid)
            resumo.falhas += 1
        else:
            if criados is None:
                resumo.ignorados += 1
            else:
                resumo.comprovantes += criados
        # Depois de cada e-mail, e nao no fim: uma queda no meio perde no
        # maximo o e-mail em andamento, que volta e e deduplicado.
        estado.ultimo_uid = uid
        estado.save(update_fields=["ultimo_uid"])
    return resumo


def remetente_de(mensagem):
    return parseaddr(str(mensagem.get("From", "")))[1].strip().lower()


# Apps de banco e o Outlook anexam PDF como application/octet-stream: o tipo
# sai dos primeiros bytes. So para esse tipo generico; um tipo declarado e
# outro (text/calendar, por exemplo) continua sendo o que diz.
ASSINATURAS = [
    (b"%PDF", "application/pdf"),
    (b"\x89PNG", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
]


def tipo_do_anexo(tipo_declarado, conteudo):
    if tipo_declarado == "image/jpg":
        return "image/jpeg"
    if tipo_declarado in TIPOS_ACEITOS:
        return tipo_declarado
    if tipo_declarado != "application/octet-stream":
        return None
    for assinatura, tipo in ASSINATURAS:
        if conteudo.startswith(assinatura):
            return tipo
    if conteudo[:4] == b"RIFF" and conteudo[8:12] == b"WEBP":
        return "image/webp"
    return None


def remetente_verificado(mensagem, remetente):
    """O Gmail confirmou que o e-mail veio mesmo de quem diz no From?

    So vale o primeiro Authentication-Results, e so se for do mx.google.com:
    e o que o Gmail carimbou ao receber. Um cabecalho igual escrito por quem
    mandou fica abaixo dele e nao conta. Passa com DMARC, ou com DKIM
    assinado pelo proprio dominio do remetente.
    """
    resultados = mensagem.get_all("Authentication-Results") or []
    if not resultados:
        return False
    carimbo = str(resultados[0]).strip().lower()
    if not carimbo.startswith("mx.google.com"):
        return False
    if re.search(r"\bdmarc=pass\b", carimbo):
        return True
    dominio = re.escape(remetente.rsplit("@", 1)[-1])
    return bool(re.search(rf"\bdkim=pass\b[^;]*header\.[id]=@?{dominio}\b", carimbo))


def anexos_de(mensagem):
    """(nome, tipo, conteudo) de cada arquivo que pode ser comprovante.

    `walk` desce tambem no e-mail encaminhado como anexo (message/rfc822).
    Imagem colada no corpo conta: o Mail do iPhone manda a foto assim.
    """
    for parte in mensagem.walk():
        if parte.is_multipart():
            continue
        conteudo = parte.get_payload(decode=True) or b""
        tipo = tipo_do_anexo(parte.get_content_type(), conteudo)
        if tipo is None:
            continue
        if tipo.startswith("image/") and len(conteudo) < TAMANHO_MINIMO_DE_IMAGEM:
            continue
        yield parte.get_filename() or f"comprovante.{tipo.split('/')[1]}", tipo, conteudo


def processar_mensagem(mensagem, *, uid, validade, ler=ler_arquivo):
    """Quantos comprovantes entraram, ou None se o e-mail nao e de ninguem."""
    remetente = remetente_de(mensagem)
    cadastro = (
        RemetenteDeComprovante.objects.select_related("conta")
        .filter(email=remetente)
        .first()
    )
    if cadastro is None or not cadastro.conta.modulo_comprovantes_ativo:
        return None

    message_id = str(mensagem.get("Message-ID") or f"<uid-{validade}-{uid}@fechacaixa>")[:255]
    # From falsificado (ex-funcionario que sabe o endereco da caixa) entra,
    # porque nada se perde, mas nao soma sem alguem olhar.
    verificado = remetente_verificado(mensagem, remetente)
    criados = 0
    for indice, (nome, tipo, conteudo) in enumerate(anexos_de(mensagem)):
        # Ja gravado numa volta que caiu no meio: nem le de novo (custa).
        if Comprovante.objects.filter(
            email_message_id=message_id, indice_anexo=indice
        ).exists():
            continue
        comprovante = registrar_comprovante(
            cadastro.conta,
            conteudo,
            tipo,
            nome,
            origem=Comprovante.Origem.EMAIL,
            remetente=remetente,
            email_message_id=message_id,
            indice_anexo=indice,
            ler=ler,
        )
        if comprovante is None:
            continue
        criados += 1
        if not verificado and comprovante.situacao == Comprovante.Situacao.OK:
            comprovante.situacao = Comprovante.Situacao.CONFERIR
            comprovante.motivo = "Remetente não verificado pelo Gmail: confira antes de somar."
            comprovante.save(update_fields=["situacao", "motivo", "updated_at"])
    return criados
