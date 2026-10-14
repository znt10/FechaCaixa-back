"""Recusas da API que a tela precisa reconhecer pelo codigo, e nao pela frase.

O corpo sai como {"detail": ..., "code": ...}: o `detail` e o que o front ja
mostra em todo erro, e o `code` e por onde ele decide trocar a tela inteira
(pagamento, empresa desativada) em vez de so exibir a mensagem.
"""

from rest_framework.exceptions import APIException

MENSAGEM_EMPRESA_DESATIVADA = (
    "Esta empresa está desativada no FechaCaixa. Fale com o responsável."
)


class _RecusaComCodigo(APIException):
    def __init__(self):
        super().__init__({"detail": self.default_detail, "code": self.default_code})


class MensalidadeBloqueada(_RecusaComCodigo):
    status_code = 402
    default_code = "mensalidade_bloqueada"
    default_detail = (
        "A mensalidade desta empresa está atrasada. Pague pelo Pix para liberar o painel."
    )


class EmpresaDesativada(_RecusaComCodigo):
    status_code = 403
    default_code = "empresa_desativada"
    default_detail = MENSAGEM_EMPRESA_DESATIVADA
