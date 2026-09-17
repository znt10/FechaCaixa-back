"""Garante que os cargos existem. Roda em todo boot, pelo entrypoint."""

from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

from app.grupos import TODOS_OS_GRUPOS


class Command(BaseCommand):
    help = "Cria os grupos de cargo do FechaCaixa e limpa permissoes orfas."

    def handle(self, *args, **options):
        criados = [
            nome
            for nome in TODOS_OS_GRUPOS
            if Group.objects.get_or_create(name=nome)[1]
        ]

        if criados:
            self.stdout.write(self.style.SUCCESS(f"Cargos criados: {', '.join(criados)}"))
        else:
            self.stdout.write("Cargos ja existiam.")

        removidas = self._limpar_permissoes_orfas()
        if removidas:
            self.stdout.write(
                f"{removidas} permissoes de modelo inexistente removidas."
            )

    def _limpar_permissoes_orfas(self):
        """Apaga permissao pendurada em modelo que nao existe mais.

        O Django so oferece essa limpeza quando o migrate roda interativo — e
        num deploy ele nunca roda. Sem isto sobraram 32 aqui, apontando para
        produto, estoque e pedido, meses depois desses modelos terem saido.
        Nao fazem nada, mas continuam sendo distribuidas por quem copiar um
        grupo, e foi uma lista dessas que quebrou o boot uma vez.
        """
        vivos = {
            modelo._meta.model_name
            for modelo in apps.get_app_config("app").get_models()
        }
        orfas = Permission.objects.filter(content_type__app_label="app").exclude(
            content_type__model__in=vivos
        )
        quantas = orfas.count()
        if quantas:
            # Apagar o content type leva as permissoes junto (CASCADE).
            ContentType.objects.filter(
                id__in=orfas.values_list("content_type_id", flat=True)
            ).delete()
        return quantas
