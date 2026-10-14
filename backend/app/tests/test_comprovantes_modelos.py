from django.contrib.auth.models import Group, User
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from app.models import Comprovante, LeituraDaCaixa, RemetenteDeComprovante

from .fabricas import conta_padrao, vincular_conta


class ModelosDeComprovanteTests(TestCase):
    def test_modulo_nasce_desligado(self):
        self.assertFalse(conta_padrao().modulo_comprovantes_ativo)

    def test_remetente_e_gravado_em_minusculas(self):
        remetente = RemetenteDeComprovante.objects.create(
            conta=conta_padrao(), email="  Maria.Silva@Gmail.COM "
        )
        remetente.refresh_from_db()
        self.assertEqual(remetente.email, "maria.silva@gmail.com")

    def test_mesmo_id_de_transacao_na_mesma_empresa_e_recusado(self):
        conta = conta_padrao()
        Comprovante.objects.create(conta=conta, origem="EMAIL", id_transacao="E1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Comprovante.objects.create(conta=conta, origem="EMAIL", id_transacao="E1")

    def test_varios_comprovantes_sem_id_convivem(self):
        """O papel da loterica nao tem ID: NULL nao colide com NULL."""
        conta = conta_padrao()
        Comprovante.objects.create(conta=conta, origem="SITE")
        Comprovante.objects.create(conta=conta, origem="SITE")
        self.assertEqual(Comprovante.objects.filter(id_transacao__isnull=True).count(), 2)

    def test_mesmo_anexo_do_mesmo_email_e_recusado(self):
        conta = conta_padrao()
        Comprovante.objects.create(
            conta=conta, origem="EMAIL", email_message_id="<m1>", indice_anexo=0
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Comprovante.objects.create(
                conta=conta, origem="EMAIL", email_message_id="<m1>", indice_anexo=0
            )

    def test_comprovante_nasce_para_conferir(self):
        comprovante = Comprovante.objects.create(conta=conta_padrao(), origem="SITE")
        self.assertEqual(comprovante.situacao, Comprovante.Situacao.CONFERIR)
        self.assertEqual(comprovante.campos_editados, [])

    def test_leitura_da_caixa_e_uma_linha_so(self):
        estado, _ = LeituraDaCaixa.objects.get_or_create(pk=1)
        self.assertIsNone(estado.ultimo_uid)


class ModuloNoMeTests(TestCase):
    def test_me_diz_se_a_empresa_tem_comprovantes(self):
        Group.objects.get_or_create(name="Gerente")
        conta = conta_padrao()
        conta.modulo_comprovantes_ativo = True
        conta.save()
        user = User.objects.create_user(username="g@x.com", password="123456")
        user.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(user, conta)
        cliente = APIClient()
        cliente.force_authenticate(user)

        resposta = cliente.get("/api/v1/user/me/")

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.data["modulos"]["comprovantes"])
