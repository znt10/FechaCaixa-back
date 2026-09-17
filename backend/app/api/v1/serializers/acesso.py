"""Entrada e saida do acesso ao formulario (o unico endpoint publico)."""

from rest_framework import serializers

from app.models import Conta


class EmpresaDoFormularioSerializer(serializers.ModelSerializer):
    """O que o formulario mostra no cabecalho e usa para montar os campos."""

    class Meta:
        model = Conta
        # O slug e o endereco publico da empresa (/primavera): depois de
        # digitar o codigo, e para la que o aparelho e mandado.
        fields = ["nome", "slug", "fechamentos_por_dia"]


class AcessoAoFormularioSerializer(serializers.Serializer):
    codigo = serializers.CharField(max_length=16)
    # Opcional: a loja tem um celular so, e obrigar quem esta fechando o caixa
    # a batizar o aparelho e uma pergunta a mais no fim do expediente. Quando
    # nao vem, o backend batiza pelo que o navegador ja conta de si.
    apelido = serializers.CharField(max_length=80, required=False, allow_blank=True)

    def validate_codigo(self, valor):
        """Aceita o codigo do jeito que a pessoa digitar.

        Ninguem digita o hifen, o teclado do celular capitaliza sozinho e colar
        traz espaco junto. "prim 4821", "PRIM4821" e "PRIM-4821" sao a mesma
        pessoa acertando o codigo — recusar qualquer um deles e a tela brigando
        com quem esta tentando fechar o caixa.
        """
        limpo = "".join(c for c in valor.upper() if c.isalnum())
        letras = "".join(c for c in limpo if c.isalpha())
        digitos = "".join(c for c in limpo if c.isdigit())
        if letras and digitos:
            return f"{letras}-{digitos}"
        return limpo

    def validate_apelido(self, valor):
        return valor.strip()
