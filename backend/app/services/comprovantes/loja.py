"""De qual loja saiu o dinheiro.

So entre as lojas da empresa do comprovante: CNPJ completo primeiro (Nubank,
PicPay), depois agencia + conta (Santander, que mascara o CNPJ). Nome nao
entra: com CNPJ e conta cadastrados ele nao faz falta, e erraria entre lojas
da mesma empresa.
"""

from app.models import ContaBancaria, Loja

from .normalizar import mesma_conta, so_digitos


def achar_loja(conta, documento="", agencia="", numero=""):
    cnpj = so_digitos(documento)
    if len(cnpj) == 14:
        loja = Loja.objects.filter(conta=conta, cnpj=cnpj).first()
        if loja is not None:
            return loja

    if so_digitos(numero):
        contas_bancarias = ContaBancaria.objects.filter(
            loja__conta=conta, ativo=True
        ).select_related("loja")
        for conta_bancaria in contas_bancarias:
            if mesma_conta(agencia, numero, conta_bancaria.agencia, conta_bancaria.numero):
                return conta_bancaria.loja

    return None
