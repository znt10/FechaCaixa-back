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
        mensagem = email.message_from_bytes(caixa.baixar(uid), policy=policy.default)
        resumo.emails += 1
        criados = processar_mensagem(mensagem, uid=uid, validade=validade, ler=ler)
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


def anexos_de(mensagem):
    """(nome, tipo, conteudo) de cada arquivo que pode ser comprovante.

    `walk` desce tambem no e-mail encaminhado como anexo (message/rfc822).
    Imagem colada no corpo conta: o Mail do iPhone manda a foto assim.
    """
    for parte in mensagem.walk():
        if parte.is_multipart():
            continue
        tipo = parte.get_content_type()
        if tipo not in TIPOS_ACEITOS:
            continue
        conteudo = parte.get_payload(decode=True) or b""
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
        if comprovante is not None:
            criados += 1
    return criados
