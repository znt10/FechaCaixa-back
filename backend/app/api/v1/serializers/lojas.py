from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import serializers

from app.models import Loja


class LojaSerializer(serializers.ModelSerializer):
    """A loja, como a gerencia a cadastra.

    Ja foi bem maior. A loja tinha login proprio (email = username), e este
    arquivo cuidava de criar esse usuario, mante-lo em sincronia quando o
    email mudava, e impedir que ela ficasse sem acesso. Nada disso existe
    mais: quem lanca o caixa entra pelo codigo da empresa no aparelho, e a
    loja nao faz login.

    Foram junto os campos que so serviam aquilo — email, telefone_whatsapp
    (era o bot identificando de qual loja vinha o pedido), tipo e gerente.
    Todos estavam vazios em todas as lojas.
    """

    id = serializers.UUIDField(source="public_id", read_only=True)

    # Declarado a mao para nascer SEM o UniqueValidator que o ModelSerializer
    # derivaria do `unique=True` do modelo. O DRF roda os validadores do campo
    # ANTES do validate_cnpj, entao aquele validador comparava o CNPJ pontuado
    # que a gerente cola do contrato ("12.345.678/0001-99") com os 14 digitos
    # ja gravados, nao achava colisao nenhuma, e a restricao do banco subia
    # crua como 500. A unicidade e conferida no validate_cnpj, depois de
    # normalizar — que e a unica ordem em que ela responde a pergunta certa.
    cnpj = serializers.CharField(
        max_length=18,
        required=False,
        allow_null=True,
        allow_blank=True,
    )

    class Meta:
        model = Loja
        fields = ["id", "nome_loja", "cidade", "endereco", "ativo", "cnpj"]

    def validate_cnpj(self, valor):
        """Normaliza, e so entao pergunta se esse CNPJ ja e de outra loja.

        Chama Loja.normalizar_cnpj que levanta ValidationError (Django) se
        quantidade de digitos e errada. Aqui capturamos e re-levantamos como
        ValidationError (DRF) para a API responder 400 com mensagem em
        portugues.

        O modelo guarda a mesma validacao no save() para quem escreve pelo ORM
        ou admin: se a validacao nao passar na API, o banco fica protegido.
        """
        try:
            valor_normalizado = Loja.normalizar_cnpj(valor)
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages[0])

        if valor_normalizado is None:
            return None

        # Sem excluir is_deleted de proposito: a restricao do banco tambem nao
        # exclui, entao uma loja arquivada continua ocupando o CNPJ e a recusa
        # aqui tem que dizer a mesma coisa que o banco diria.
        outras = Loja.objects.filter(cnpj=valor_normalizado)
        if self.instance is not None:
            outras = outras.exclude(pk=self.instance.pk)
        if outras.exists():
            # A frase nao diz de quem e a loja: o CNPJ e unico no sistema
            # inteiro, e apontar a empresa vizinha entregaria inquilino para
            # inquilino.
            raise serializers.ValidationError(
                "Ja existe uma loja cadastrada com este CNPJ."
            )

        return valor_normalizado
