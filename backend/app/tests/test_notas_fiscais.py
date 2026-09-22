import datetime
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient, APITestCase

from app.api.v1.serializers import LojaSerializer
from app.models import Conta, Loja

from .fabricas import conta_padrao, criar_loja, vincular_conta

FIXTURES_NFE = Path(__file__).resolve().parent / "fixtures" / "nfe"


def xml_de(nome):
    return (FIXTURES_NFE / nome).read_text(encoding="utf-8")


class CnpjDaLojaTests(TestCase):
    def test_guarda_so_os_digitos(self):
        """A gerente cola o CNPJ formatado do contrato; o XML traz 14 digitos.

        Se os dois formatos entrassem no banco, a nota nunca acharia a loja.
        """
        loja = criar_loja(
            nome_loja="Loja A",
            cidade="Patos",
            endereco="Rua 1",
            cnpj="12.345.678/0001-99",
        )
        loja.refresh_from_db()
        self.assertEqual(loja.cnpj, "12345678000199")

    def test_loja_sem_cnpj_continua_valida(self):
        """Toda loja que ja existe esta sem CNPJ, e nao pode quebrar."""
        loja = criar_loja(nome_loja="Loja B", cidade="Patos", endereco="Rua 2")
        loja.refresh_from_db()
        self.assertIsNone(loja.cnpj)

    def test_duas_lojas_sem_cnpj_convivem(self):
        """NULL nao colide com NULL; string vazia colidiria, e por isso e NULL."""
        criar_loja(nome_loja="Loja C", cidade="Patos", endereco="Rua 3")
        criar_loja(nome_loja="Loja D", cidade="Patos", endereco="Rua 4")
        self.assertEqual(Loja.objects.filter(cnpj__isnull=True).count(), 2)

    def test_normalizar_devolve_none_para_vazio(self):
        self.assertIsNone(Loja.normalizar_cnpj(""))
        self.assertIsNone(Loja.normalizar_cnpj(None))

    def test_serializer_aceita_cnpj_formatado(self):
        """O serializer (e o admin) precisam aceitar o CNPJ formatado da gerente.

        A validacao de tamanho do DRF roda antes de save(): max_length=18
        permite a entrada, e save() normaliza para os 14 digitos.
        """
        conta = criar_loja(nome_loja="Dummy", cidade="X", endereco="Y").conta
        serializer = LojaSerializer(
            data={
                "nome_loja": "Loja E",
                "cidade": "Patos",
                "endereco": "Rua 5",
                "ativo": True,
                "cnpj": "12.345.678/0001-99",
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        loja = serializer.save(conta=conta)
        self.assertEqual(loja.cnpj, "12345678000199")

    def test_normalizar_rejeita_quantidade_errada_de_digitos(self):
        """Se nao tem exatamente 14 digitos, a normalizacao levanta erro."""
        with self.assertRaises(ValidationError) as cm:
            Loja.normalizar_cnpj("123")
        self.assertIn("CNPJ invalido", str(cm.exception))


class CnpjDaLojaAPITests(APITestCase):
    """Testes da API que batem no endpoint de verdade, nao no serializer isolado.

    O serializer isolado passou em rodadas anteriores e escondeu que a API
    levantava 500 em vez de 400 para CNPJ invalido.
    """

    def setUp(self):
        grupo_admin, _ = Group.objects.get_or_create(name="Admin")
        self.admin = User.objects.create_user(username="admin", password="123456")
        self.admin.groups.add(grupo_admin)
        vincular_conta(self.admin)
        self.client.force_authenticate(self.admin)

    def test_post_com_cnpj_formatado_normaliza_e_salva_201(self):
        """POST com CNPJ formatado deve aceitar, normalizar e responder 201."""
        resp = self.client.post(
            "/api/v1/lojas/",
            {
                "nome_loja": "Loja Formatada",
                "cidade": "Patos",
                "endereco": "Rua X",
                "cnpj": "12.345.678/0001-99",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["cnpj"], "12345678000199")

    def test_post_com_cnpj_invalido_responde_400_com_mensagem(self):
        """POST com CNPJ de quantidade errada de digitos responde 400 (nao 500).

        Antes da validacao no serializer, isto levantava 500 porque a
        ValidationError do modelo nao era capturada.
        """
        resp = self.client.post(
            "/api/v1/lojas/",
            {
                "nome_loja": "Loja Invalida",
                "cidade": "Patos",
                "endereco": "Rua Y",
                "cnpj": "1234567890123456",  # 16 digitos, valida max_length mas nao a logica
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("cnpj", resp.data)
        self.assertEqual(
            resp.data["cnpj"][0],
            "CNPJ invalido: esperado 14 digitos, veio 16."
        )


    def test_post_com_cnpj_formatado_ja_cadastrado_responde_400(self):
        """O caminho que motivou o max_length=18: a gerente colando o contrato.

        O CNPJ ja esta no banco em 14 digitos. Enviado pontuado, ele passava
        pela checagem de unicidade (que comparava 18 chars com 14 digitos),
        chegava ao banco normalizado e a restricao subia crua como 500. A
        normalizacao tem que vir antes da pergunta "esse CNPJ ja e de alguem".
        """
        criar_loja(
            nome_loja="Loja Ja Cadastrada", cidade="Patos", endereco="Rua W",
            cnpj="12345678000199",
        )

        resp = self.client.post(
            "/api/v1/lojas/",
            {
                "nome_loja": "Loja Repetida",
                "cidade": "Patos",
                "endereco": "Rua Z",
                "cnpj": "12.345.678/0001-99",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(
            resp.data["cnpj"][0], "Ja existe uma loja cadastrada com este CNPJ."
        )
        self.assertEqual(Loja.objects.filter(cnpj="12345678000199").count(), 1)

    def test_post_com_cnpj_cru_ja_cadastrado_responde_400(self):
        """O mesmo CNPJ sem pontuacao ja respondia 400 — continua respondendo,
        e agora com a mesma frase do caminho pontuado."""
        criar_loja(
            nome_loja="Loja Ja Cadastrada", cidade="Patos", endereco="Rua W",
            cnpj="12345678000199",
        )

        resp = self.client.post(
            "/api/v1/lojas/",
            {
                "nome_loja": "Loja Repetida",
                "cidade": "Patos",
                "endereco": "Rua Z",
                "cnpj": "12345678000199",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(
            resp.data["cnpj"][0], "Ja existe uma loja cadastrada com este CNPJ."
        )

    def test_salvar_a_loja_sem_trocar_o_cnpj_nao_e_repeticao(self):
        """Corrigir o endereco nao pode ser recusado pelo proprio CNPJ da loja."""
        loja = criar_loja(
            nome_loja="Loja Unica", cidade="Patos", endereco="Rua W",
            cnpj="12345678000199",
        )

        resp = self.client.patch(
            f"/api/v1/lojas/{loja.public_id}/",
            {"endereco": "Rua Nova", "cnpj": "12.345.678/0001-99"},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        loja.refresh_from_db()
        self.assertEqual(loja.endereco, "Rua Nova")
        self.assertEqual(loja.cnpj, "12345678000199")


class LeitorDeNFeTests(TestCase):
    def test_le_o_formato_com_protocolo(self):
        from app.services.nfe import ler_nfe

        dados = ler_nfe(xml_de("nfe_proc.xml"))

        self.assertEqual(
            dados.chave, "35260712345678000199550010000012341000012345"
        )
        self.assertEqual(dados.numero, "1234")
        self.assertEqual(dados.serie, "1")
        self.assertEqual(dados.data_emissao, datetime.date(2026, 7, 15))
        self.assertEqual(dados.valor_total, Decimal("800.00"))
        self.assertEqual(dados.cnpj_emitente, "12345678000199")
        self.assertEqual(dados.nome_emitente, "Embalagens do Vale LTDA")
        self.assertEqual(dados.cnpj_destinatario, "98765432000188")

    def test_le_a_nota_sem_o_protocolo_em_volta(self):
        """Pelo e-mail chegam os dois formatos, e os dois precisam entrar."""
        from app.services.nfe import ler_nfe

        dados = ler_nfe(xml_de("nfe_cru.xml"))

        self.assertEqual(dados.numero, "9999")
        self.assertEqual(dados.valor_total, Decimal("250.50"))
        self.assertEqual(dados.nome_emitente, "Posto Central")

    def test_le_a_versao_antiga_com_dEmi(self):
        """A 3.10 traz so a data, sem hora. Fornecedor pequeno ainda emite."""
        from app.services.nfe import ler_nfe

        dados = ler_nfe(xml_de("nfe_310_demi.xml"))

        self.assertEqual(dados.data_emissao, datetime.date(2026, 6, 30))

    def test_recusa_xml_que_nao_e_nota(self):
        from app.services.nfe import XmlNaoEhNFe, ler_nfe

        with self.assertRaises(XmlNaoEhNFe):
            ler_nfe(xml_de("nao_e_nfe.xml"))

    def test_recusa_arquivo_corrompido(self):
        """Anexo truncado no download: nao pode virar 500 no upload."""
        from app.services.nfe import XmlNaoEhNFe, ler_nfe

        with self.assertRaises(XmlNaoEhNFe):
            ler_nfe("<nfeProc><infNFe")

    def test_recusa_bomba_de_entidades(self):
        """DOCTYPE com entidades aninhadas expande na memoria ao fazer parse.

        194 bytes em disco viram 1000 caracteres so com dois niveis de
        entidade — nove niveis chegam a gigabytes. O teto de tamanho do
        upload nao ve isso chegar, porque o arquivo e pequeno; a nota real da
        SEFAZ nunca tem DOCTYPE, entao recusar qualquer DOCTYPE nao custa nada
        a quem sobe nota de verdade.
        """
        from app.services.nfe import XmlNaoEhNFe, ler_nfe

        # Igualdade exata e nao so assertRaises: sem isso o teste passaria
        # tambem se a nota fosse recusada por nao ter infNFe (que e verdade
        # aqui, mas e a razao errada) e nunca provaria que o guard de DOCTYPE
        # existe.
        with self.assertRaises(XmlNaoEhNFe) as cm:
            ler_nfe(xml_de("bomba_de_entidades.xml"))
        self.assertEqual(
            str(cm.exception),
            "O arquivo tem uma declaracao DOCTYPE, que uma NFe nunca tem.",
        )

    def test_recusa_bomba_de_entidades_disfarcada_em_utf16(self):
        """Achado do fix round 1: o guard original comparava texto literal
        contra "<!DOCTYPE" e tinha bypass.

        Uma bomba salva em UTF-16LE, depois de um `.decode("utf-8")`
        ESTRITO (sem errors="replace" — o decode nao falha, porque todo byte
        de um UTF-16LE so-ASCII e um codepoint UTF-8 valido sozinho), vira
        uma string com um NUL entre cada caractere original. "<!DOCTYPE"
        nunca aparece literal nela, e o regex antigo nao pegava — pior, o
        acelerador C do ElementTree reconstruia essa string de volta para os
        bytes UTF-16LE originais ao fazer o parse, e a bomba expandia
        normalmente mesmo assim. Medido, nao teoria.

        A mensagem esperada e a do DOCTYPE, e isso e o teste inteiro: e ela
        que prova que quem barrou foi o `StartDoctypeDeclHandler`, olhando o
        mesmo texto que o `fromstring` olharia. "O arquivo nao e um XML
        valido." aqui significaria que a guarda condenou por incoerencia de
        codificacao, ou seja, julgando um documento diferente do que o parser
        de verdade veria — que e a classe de bug que originou este teste.
        """
        from app.services.nfe import XmlNaoEhNFe, ler_nfe

        bomba_disfarcada = (
            xml_de("bomba_de_entidades.xml").encode("utf-16-le").decode("utf-8")
        )

        with self.assertRaises(XmlNaoEhNFe) as cm:
            ler_nfe(bomba_disfarcada)
        self.assertEqual(
            str(cm.exception),
            "O arquivo tem uma declaracao DOCTYPE, que uma NFe nunca tem.",
        )

    def test_bomba_disfarcada_em_utf16_nao_e_parseada_nem_expandida(self):
        """A mesma bomba em UTF-16LE, mas sem nada em que a guarda possa se
        apoiar por acidente: aqui o arquivo nao traz declaracao de
        codificacao nenhuma, entao nao ha incoerencia "declara UTF-8 mas os
        bytes sao UTF-16" para o expat reclamar. Sobra o handler de DOCTYPE.

        Existe porque a guarda ja passou uma rodada inteira barrando esta
        familia de arquivos pelo motivo errado, e ninguem percebeu: os testes
        aceitavam "O arquivo nao e um XML valido.", que e o que a checagem de
        codificacao devolve. Este caso nao tem esse atalho — se o handler
        sumir, `fromstring` parseia a bomba e a expande (194 bytes viram 1000
        caracteres; nove niveis chegam a gigabytes), a raiz "lolz" nao tem
        infNFe, e a recusa sai com outra mensagem. Por isso a igualdade
        exata: e ela que separa "recusou antes de expandir" de "recusou
        depois de expandir".
        """
        from app.services.nfe import XmlNaoEhNFe, ler_nfe

        sem_declaracao = xml_de("bomba_de_entidades.xml").split("?>", 1)[1].lstrip()
        bomba_disfarcada = sem_declaracao.encode("utf-16-le").decode("utf-8")

        with self.assertRaises(XmlNaoEhNFe) as cm:
            ler_nfe(bomba_disfarcada)
        self.assertEqual(
            str(cm.exception),
            "O arquivo tem uma declaracao DOCTYPE, que uma NFe nunca tem.",
        )

    def test_texto_impossivel_de_codificar_vira_recusa_e_nao_erro_cru(self):
        """`ler_nfe` promete no topo do modulo: texto entra, dados saem, ou
        XmlNaoEhNFe. Uma str com surrogate (que nao vem da view, cujo decode
        estrito nunca gera uma, mas vem de quem chamar o modulo direto) faz o
        pyexpat estourar UnicodeEncodeError ao converter a str para utf-8 por
        dentro. Sem tratamento, isso vaza como terceiro tipo de excecao e
        quem chama nao tem como recusar so aquele arquivo.
        """
        from app.services.nfe import XmlNaoEhNFe, ler_nfe

        with self.assertRaises(XmlNaoEhNFe):
            ler_nfe("<lolz>\ud800</lolz>")

    def test_destinatario_por_cpf_fica_sem_cnpj(self):
        """Nota emitida para pessoa fisica: nao ha CNPJ para casar com loja."""
        from app.services.nfe import ler_nfe

        xml = xml_de("nfe_cru.xml").replace(
            "<CNPJ>98765432000188</CNPJ>", "<CPF>12345678901</CPF>"
        )
        self.assertIsNone(ler_nfe(xml).cnpj_destinatario)


class PlanoDeContasTests(TestCase):
    def setUp(self):
        from .fabricas import conta_padrao

        self.conta = conta_padrao()

    def test_semeia_os_quatro_grupos(self):
        from app.models import GrupoDeDespesa
        from app.services.plano_de_contas import garantir_plano_de_contas

        garantir_plano_de_contas(self.conta)

        nomes = set(
            GrupoDeDespesa.objects.filter(conta=self.conta).values_list(
                "nome", flat=True
            )
        )
        self.assertEqual(nomes, {"Compras", "Operacional", "Pessoal", "Impostos"})

    def test_semear_duas_vezes_nao_duplica(self):
        """Roda a cada upload: se duplicasse, a lista da tela cresceria sozinha."""
        from app.models import ElementoDeDespesa, GrupoDeDespesa
        from app.services.plano_de_contas import garantir_plano_de_contas

        garantir_plano_de_contas(self.conta)
        antes = (
            GrupoDeDespesa.objects.count(),
            ElementoDeDespesa.objects.count(),
        )
        garantir_plano_de_contas(self.conta)

        self.assertEqual(
            (GrupoDeDespesa.objects.count(), ElementoDeDespesa.objects.count()),
            antes,
        )

    def test_nao_reintroduz_elemento_que_a_conta_apagou(self):
        """Semear de novo nao pode desfazer a arrumacao que a empresa fez."""
        from app.models import ElementoDeDespesa
        from app.services.plano_de_contas import garantir_plano_de_contas

        garantir_plano_de_contas(self.conta)
        ElementoDeDespesa.objects.filter(nome="Combustivel").delete()
        garantir_plano_de_contas(self.conta)

        self.assertFalse(ElementoDeDespesa.objects.filter(nome="Combustivel").exists())

    def test_uma_conta_nao_ve_o_plano_da_outra(self):
        from app.models import Conta, GrupoDeDespesa
        from app.services.plano_de_contas import garantir_plano_de_contas

        outra = Conta.objects.create(nome="Outro negocio")
        garantir_plano_de_contas(self.conta)
        garantir_plano_de_contas(outra)

        self.assertEqual(GrupoDeDespesa.objects.filter(conta=self.conta).count(), 4)
        self.assertEqual(GrupoDeDespesa.objects.filter(conta=outra).count(), 4)


class FornecedorTests(TestCase):
    def test_mesmo_cnpj_na_mesma_conta_e_uma_linha_so(self):
        """O fornecedor atende as 8 lojas; duplicado, a sugestao nao aprende."""
        from django.db import IntegrityError

        from app.models import Fornecedor

        from .fabricas import conta_padrao

        conta = conta_padrao()
        Fornecedor.objects.create(
            conta=conta, cnpj="12345678000199", razao_social="Embalagens"
        )
        with self.assertRaises(IntegrityError):
            Fornecedor.objects.create(
                conta=conta, cnpj="12345678000199", razao_social="Embalagens SA"
            )


class NotaFiscalModeloTests(TestCase):
    def test_chave_e_unica(self):
        """A chave de 44 digitos e a defesa contra lancar a mesma nota duas vezes."""
        from django.db import IntegrityError

        from app.models import Fornecedor, NotaFiscal

        from .fabricas import conta_padrao, criar_loja

        conta = conta_padrao()
        loja = criar_loja(
            nome_loja="Loja A", cidade="Patos", endereco="Rua 1",
            cnpj="98765432000188",
        )
        fornecedor = Fornecedor.objects.create(
            conta=conta, cnpj="12345678000199", razao_social="Embalagens"
        )
        campos = dict(
            conta=conta,
            loja=loja,
            fornecedor=fornecedor,
            chave="3" * 44,
            numero="1",
            serie="1",
            data_emissao=datetime.date(2026, 7, 15),
            valor_total=Decimal("10.00"),
            xml_bruto="<NFe/>",
        )
        NotaFiscal.objects.create(**campos)
        with self.assertRaises(IntegrityError):
            NotaFiscal.objects.create(**campos)

    def test_nasce_sem_classificacao(self):
        """Nao classificada e estado legitimo: e a fila de trabalho da tela."""
        from app.models import Fornecedor, NotaFiscal

        from .fabricas import conta_padrao, criar_loja

        conta = conta_padrao()
        nota = NotaFiscal.objects.create(
            conta=conta,
            loja=criar_loja(
                nome_loja="Loja A", cidade="Patos", endereco="Rua 1",
                cnpj="98765432000188",
            ),
            fornecedor=Fornecedor.objects.create(
                conta=conta, cnpj="12345678000199", razao_social="Embalagens"
            ),
            chave="4" * 44,
            numero="1",
            serie="1",
            data_emissao=datetime.date(2026, 7, 15),
            valor_total=Decimal("10.00"),
            xml_bruto="<NFe/>",
        )
        self.assertIsNone(nota.elemento)
        self.assertFalse(nota.classificada)


class ImportacaoDeNotaTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from .fabricas import conta_padrao, criar_loja, vincular_conta

        self.conta = conta_padrao()
        self.loja = criar_loja(
            nome_loja="Loja A", cidade="Patos", endereco="Rua 1",
            cnpj="98765432000188",
        )
        self.user = User.objects.create_user(username="ger@x.com", password="123456")
        vincular_conta(self.user, self.conta)

    def test_importa_e_acha_a_loja_pelo_cnpj_do_destinatario(self):
        from app.services.notas import importar_nota

        nota = importar_nota(xml_de("nfe_proc.xml"), self.conta, self.user)

        self.assertEqual(nota.loja, self.loja)
        self.assertEqual(nota.valor_total, Decimal("800.00"))
        self.assertEqual(nota.enviada_por, self.user)
        self.assertEqual(nota.xml_bruto, xml_de("nfe_proc.xml"))

    def test_cria_o_fornecedor_na_primeira_nota_dele(self):
        from app.models import Fornecedor
        from app.services.notas import importar_nota

        nota = importar_nota(xml_de("nfe_proc.xml"), self.conta, self.user)

        self.assertEqual(nota.fornecedor.cnpj, "12345678000199")
        self.assertEqual(nota.fornecedor.razao_social, "Embalagens do Vale LTDA")
        self.assertEqual(Fornecedor.objects.filter(conta=self.conta).count(), 1)

    def test_a_nota_ja_chega_com_a_sugestao_do_fornecedor(self):
        """A regra de nao digitar duas vezes o mesmo fornecedor."""
        from app.models import ElementoDeDespesa, Fornecedor
        from app.services.notas import importar_nota
        from app.services.plano_de_contas import garantir_plano_de_contas

        garantir_plano_de_contas(self.conta)
        embalagem = ElementoDeDespesa.objects.get(
            nome="Embalagem", grupo__conta=self.conta
        )
        Fornecedor.objects.create(
            conta=self.conta,
            cnpj="12345678000199",
            razao_social="Embalagens do Vale LTDA",
            elemento_sugerido=embalagem,
        )

        nota = importar_nota(xml_de("nfe_proc.xml"), self.conta, self.user)

        self.assertEqual(nota.elemento, embalagem)

    def test_recusa_a_mesma_nota_duas_vezes(self):
        """A recusa mais comum e a mais importante: e ela que evita contar duas vezes.

        Dispara NotaJaLancada: a primeira importacao e aceita normalmente, a
        segunda encontra a mesma chave ja gravada.
        """
        from django.utils import timezone

        from app.services.notas import NotaJaLancada, importar_nota

        primeira = importar_nota(xml_de("nfe_proc.xml"), self.conta, self.user)

        data_primeira = timezone.localtime(primeira.created_at).strftime("%d/%m/%Y")
        mensagem_esperada = (
            f"A nota 1234 ja foi lancada em {data_primeira} por ger@x.com."
        )
        with self.assertRaises(NotaJaLancada) as cm:
            importar_nota(xml_de("nfe_proc.xml"), self.conta, self.user)
        self.assertEqual(cm.exception.mensagem, mensagem_esperada)

    def test_recusa_a_mesma_nota_lancada_por_outra_conta_sem_vazar_dados(self):
        """A chave e unica no Brasil inteiro: a mesma nota pode ja existir numa
        outra empresa do sistema. A mensagem nao pode entregar data nem
        usuario daquela empresa para quem esta subindo agora."""
        from django.contrib.auth.models import User

        from app.models import Conta
        from app.services.notas import NotaJaLancada, importar_nota

        from .fabricas import criar_loja, vincular_conta

        outra_conta = Conta.objects.create(nome="Outra empresa")
        # CNPJ distinto do emitente e da loja desta conta, so para a nota
        # achar uma loja em `outra_conta` sem disparar NotaDeSaida.
        criar_loja(
            nome_loja="Loja B", cidade="Sousa", endereco="Rua 2",
            cnpj="11222333000181", conta=outra_conta,
        )
        outro_user = User.objects.create_user(
            username="dono@outraempresa.com", password="123456"
        )
        vincular_conta(outro_user, outra_conta)
        # A nota 1234 e lancada primeiro na outra conta, com o destinatario
        # trocado para a loja de la.
        xml_da_outra_conta = xml_de("nfe_proc.xml").replace(
            "<CNPJ>98765432000188</CNPJ>", "<CNPJ>11222333000181</CNPJ>"
        )
        importar_nota(xml_da_outra_conta, outra_conta, outro_user)

        with self.assertRaises(NotaJaLancada) as cm:
            importar_nota(xml_de("nfe_proc.xml"), self.conta, self.user)
        self.assertEqual(
            cm.exception.mensagem, "A nota 1234 ja consta como lancada no sistema."
        )
        self.assertNotIn("dono@outraempresa.com", cm.exception.mensagem)

    def test_recusa_nota_de_cnpj_que_nao_e_loja_da_conta(self):
        """Dispara DestinatarioNaoEhDaConta: o CNPJ do destinatario existe mas
        nao e o de nenhuma loja cadastrada nesta conta."""
        from app.services.notas import DestinatarioNaoEhDaConta, importar_nota

        xml = xml_de("nfe_proc.xml").replace(
            "<CNPJ>98765432000188</CNPJ>", "<CNPJ>00000000000191</CNPJ>"
        )
        mensagem_esperada = (
            "A nota 1234 foi emitida para 00.000.000/0001-91, que nao e o "
            "CNPJ de nenhuma loja sua. Confira o cadastro da loja."
        )
        with self.assertRaises(DestinatarioNaoEhDaConta) as cm:
            importar_nota(xml, self.conta, self.user)
        self.assertEqual(cm.exception.mensagem, mensagem_esperada)

    def test_recusa_nota_emitida_pela_propria_loja(self):
        """Nota de saida e venda. Sem esta recusa, o faturamento vira gasto.

        Dispara NotaDeSaida: o CNPJ do emitente e o de uma loja da propria
        conta, entao o que aconteceu foi uma venda entre lojas do grupo, nao
        uma compra."""
        from app.services.notas import NotaDeSaida, importar_nota

        xml = xml_de("nfe_proc.xml").replace(
            "<CNPJ>12345678000199</CNPJ>", "<CNPJ>98765432000188</CNPJ>"
        )
        mensagem_esperada = (
            "A nota 1234 foi emitida pela sua propria loja "
            "(98.765.432/0001-88). Isso e venda, nao despesa."
        )
        with self.assertRaises(NotaDeSaida) as cm:
            importar_nota(xml, self.conta, self.user)
        self.assertEqual(cm.exception.mensagem, mensagem_esperada)

    def test_nao_casa_a_loja_da_empresa_vizinha_pelo_cnpj(self):
        """A camada que decide a que empresa a despesa pertence.

        A busca da loja pelo CNPJ do destinatario e o unico lugar onde isso e
        decidido. Sem o escopo de conta nela, uma NFe emitida para o CNPJ da
        loja da padaria vizinha entraria com `conta` desta empresa e `loja` da
        outra: despesa de terceiro no razao desta, e uma nota apontando para
        fora da propria conta. A recusa e o que impede isso.
        """
        from app.models import NotaFiscal
        from app.services.notas import DestinatarioNaoEhDaConta, importar_nota

        outra_conta = Conta.objects.create(nome="Padaria vizinha")
        criar_loja(
            nome_loja="Loja da vizinha", cidade="Sousa", endereco="Rua 9",
            cnpj="11222333000181", conta=outra_conta,
        )
        xml = xml_de("nfe_proc.xml").replace(
            "<CNPJ>98765432000188</CNPJ>", "<CNPJ>11222333000181</CNPJ>"
        )
        mensagem_esperada = (
            "A nota 1234 foi emitida para 11.222.333/0001-81, que nao e o "
            "CNPJ de nenhuma loja sua. Confira o cadastro da loja."
        )

        with self.assertRaises(DestinatarioNaoEhDaConta) as cm:
            importar_nota(xml, self.conta, self.user)

        self.assertEqual(cm.exception.mensagem, mensagem_esperada)
        # A recusa sozinha nao provaria o isolamento: e a ausencia de nota que
        # diz que a despesa da vizinha nao entrou em lugar nenhum.
        self.assertEqual(NotaFiscal.objects.count(), 0)

    def test_a_loja_da_empresa_vizinha_nao_transforma_compra_em_venda(self):
        """A mesma decisao, do outro lado: o emitente tambem e por conta.

        O CNPJ que emitiu esta nota e de uma loja da padaria vizinha — para
        esta empresa e um fornecedor como qualquer outro. Sem o escopo de
        conta na recusa de saida, a compra seria recusada como se fosse venda
        da propria empresa, e a mensagem chamaria de "sua propria loja" o CNPJ
        de outra gente.
        """
        from app.services.notas import importar_nota

        outra_conta = Conta.objects.create(nome="Padaria vizinha")
        criar_loja(
            nome_loja="Loja da vizinha", cidade="Sousa", endereco="Rua 9",
            cnpj="11222333000181", conta=outra_conta,
        )
        xml = xml_de("nfe_proc.xml").replace(
            "<CNPJ>12345678000199</CNPJ>", "<CNPJ>11222333000181</CNPJ>"
        )

        nota = importar_nota(xml, self.conta, self.user)

        self.assertEqual(nota.conta, self.conta)
        self.assertEqual(nota.loja, self.loja)
        self.assertEqual(nota.fornecedor.cnpj, "11222333000181")

    def test_recusa_nota_para_pessoa_fisica(self):
        """Dispara DestinatarioNaoEhDaConta: a nota foi emitida para CPF, entao
        nao ha CNPJ nenhum para casar com loja."""
        from app.services.notas import DestinatarioNaoEhDaConta, importar_nota

        xml = xml_de("nfe_proc.xml").replace(
            "<CNPJ>98765432000188</CNPJ>", "<CPF>12345678901</CPF>"
        )
        mensagem_esperada = (
            "A nota 1234 foi emitida para uma pessoa fisica (CPF), e por isso "
            "nao e despesa de loja nenhuma sua."
        )
        with self.assertRaises(DestinatarioNaoEhDaConta) as cm:
            importar_nota(xml, self.conta, self.user)
        self.assertEqual(cm.exception.mensagem, mensagem_esperada)

    def test_a_nota_recusada_nao_deixa_fornecedor_orfao(self):
        """Recusa e tudo-ou-nada: fornecedor sem nota sujaria a lista da tela.

        Dispara DestinatarioNaoEhDaConta, pelo mesmo motivo do teste de CNPJ
        que nao e loja da conta. A recusa acontece antes do fornecedor ser
        criado, entao nao ha nada para a transacao desfazer aqui — mas
        garante que nenhum caminho futuro do service passe a criar o
        fornecedor antes de todas as recusas serem checadas."""
        from app.models import Fornecedor
        from app.services.notas import DestinatarioNaoEhDaConta, importar_nota

        xml = xml_de("nfe_proc.xml").replace(
            "<CNPJ>98765432000188</CNPJ>", "<CNPJ>00000000000191</CNPJ>"
        )
        mensagem_esperada = (
            "A nota 1234 foi emitida para 00.000.000/0001-91, que nao e o "
            "CNPJ de nenhuma loja sua. Confira o cadastro da loja."
        )
        with self.assertRaises(DestinatarioNaoEhDaConta) as cm:
            importar_nota(xml, self.conta, self.user)
        self.assertEqual(cm.exception.mensagem, mensagem_esperada)

        self.assertEqual(Fornecedor.objects.count(), 0)


class FlagDoModuloTests(TestCase):
    def test_nasce_desligada(self):
        """Default False: nenhum cliente de hoje ve mudanca nenhuma."""
        from .fabricas import conta_padrao

        self.assertFalse(conta_padrao().modulo_notas_ativo)


class ModuloDeNotasAtivoPermissionTests(TestCase):
    """Testes da permission ModuloDeNotasAtivo, chamada direto sem HTTP.

    Quatro casos: flag ON, flag OFF, superuser e usuario sem conta. Cada um
    tem code path diferente na permission e e preciso que as quatro maneiras
    de retornar False, ou retornar True, passem no teste. Um teste passando
    para flag OFF e para "sem conta" (ambos retornam None do get_conta_do_usuario)
    esconderia uma inversao na logica.
    """

    def setUp(self):
        from django.contrib.auth.models import User
        from .fabricas import conta_padrao, vincular_conta

        self.permission = self._import_permission()

        # User com flag ON
        self.conta_ativa = conta_padrao()
        self.conta_ativa.modulo_notas_ativo = True
        self.conta_ativa.save()
        self.user_ativo = User.objects.create_user(
            username="user_ativo", password="123456"
        )
        vincular_conta(self.user_ativo, self.conta_ativa)

        # User com flag OFF
        self.conta_inativa = Conta.objects.create(nome="Conta desligada")
        self.conta_inativa.modulo_notas_ativo = False
        self.conta_inativa.save()
        self.user_inativo = User.objects.create_user(
            username="user_inativo", password="123456"
        )
        vincular_conta(self.user_inativo, self.conta_inativa)

        # Superuser
        self.superuser = User.objects.create_user(
            username="superuser", password="123456", is_superuser=True
        )

        # User autenticado sem conta
        self.user_sem_conta = User.objects.create_user(
            username="sem_conta", password="123456"
        )

    def _import_permission(self):
        from app.permissions import ModuloDeNotasAtivo
        return ModuloDeNotasAtivo()

    def _fake_request(self, user):
        """Uma request minima com so o user attribute."""
        class FakeRequest:
            pass
        req = FakeRequest()
        req.user = user
        return req

    def test_user_com_flag_ON_tem_acesso(self):
        """Conta com modulo_notas_ativo=True → has_permission retorna True."""
        from app.permissions import get_conta_do_usuario

        request = self._fake_request(self.user_ativo)
        conta = get_conta_do_usuario(self.user_ativo)

        # Confirma a setup
        self.assertTrue(conta.modulo_notas_ativo)

        # Testa a permission
        result = self.permission.has_permission(request, None)
        self.assertTrue(result, "User com flag ON deveria ter acesso")

    def test_user_com_flag_OFF_nao_tem_acesso(self):
        """Conta com modulo_notas_ativo=False → has_permission retorna False."""
        from app.permissions import get_conta_do_usuario

        request = self._fake_request(self.user_inativo)
        conta = get_conta_do_usuario(self.user_inativo)

        # Confirma a setup
        self.assertFalse(conta.modulo_notas_ativo)

        # Testa a permission
        result = self.permission.has_permission(request, None)
        self.assertFalse(result, "User com flag OFF nao deveria ter acesso")

    def test_superuser_tem_acesso(self):
        """Superuser e dono da plataforma e ve tudo."""
        from app.permissions import get_conta_do_usuario

        request = self._fake_request(self.superuser)
        conta = get_conta_do_usuario(self.superuser)

        # Confirma que superuser retorna None de get_conta_do_usuario
        self.assertIsNone(conta, "Superuser deveria retornar None (ve tudo)")

        # Testa a permission
        result = self.permission.has_permission(request, None)
        self.assertTrue(result, "Superuser deveria ter acesso")

    def test_user_sem_conta_nao_tem_acesso(self):
        """Usuario autenticado sem perfil vinculado → sem acesso."""
        from app.permissions import get_conta_do_usuario

        request = self._fake_request(self.user_sem_conta)
        conta = get_conta_do_usuario(self.user_sem_conta)

        # Confirma que sem conta retorna None de get_conta_do_usuario
        self.assertIsNone(
            conta,
            "User sem conta vinculada deveria retornar None (ve nada)"
        )

        # Testa a permission
        result = self.permission.has_permission(request, None)
        self.assertFalse(result, "User sem conta nao deveria ter acesso")


class NotaFiscalSerializerTests(TestCase):
    def test_so_o_elemento_e_gravavel(self):
        """A classificacao abriu UM campo, e so um: a escrita do elemento.

        A lista e comparada inteira, e nao com um `assertNotIn` campo a
        campo, porque o risco nao e alguem destravar `numero` de proposito —
        e alguem apagar o `extra_kwargs` ou trocar o `fields` e destravar
        varios de uma vez sem perceber. Igualdade exata e o unico jeito de o
        teste falar do campo que ainda nem existe.
        """
        from app.api.v1.serializers import NotaFiscalSerializer

        gravaveis = [
            nome
            for nome, campo in NotaFiscalSerializer().fields.items()
            if not campo.read_only
        ]
        self.assertEqual(gravaveis, ["elemento_id"])

    def test_os_quatro_campos_de_prova_continuam_presos(self):
        """A nota e a prova da despesa: numero, serie, data e valor vem do
        XML, que fica intacto em xml_bruto. Se algum deles virasse gravavel,
        o relatorio e a prova passariam a discordar em silencio.

        Separado do teste de cima de proposito: aquele guarda o tamanho da
        porta que a classificacao abriu, este guarda o que nunca pode entrar
        por ela, e os dois falham por motivos diferentes.
        """
        from app.api.v1.serializers import NotaFiscalSerializer

        campos = NotaFiscalSerializer().fields
        for nome in ("numero", "serie", "data_emissao", "valor_total"):
            with self.subTest(campo=nome):
                self.assertTrue(
                    campos[nome].read_only,
                    f"O campo de prova '{nome}' virou gravavel.",
                )


class ApiDeNotasTests(TestCase):
    def setUp(self):
        for nome in ("Admin", "Gerente"):
            Group.objects.get_or_create(name=nome)

        self.conta = conta_padrao()
        self.conta.modulo_notas_ativo = True
        self.conta.save()

        self.loja = criar_loja(
            nome_loja="Loja A", cidade="Patos", endereco="Rua 1",
            cnpj="98765432000188",
        )
        self.user = User.objects.create_user(username="ger@x.com", password="123456")
        self.user.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(self.user, self.conta)

        self.client_api = APIClient()
        self.client_api.force_authenticate(self.user)

    def _arquivo(self, nome_da_fixture, nome_do_arquivo="nota.xml"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(
            nome_do_arquivo,
            xml_de(nome_da_fixture).encode("utf-8"),
            content_type="text/xml",
        )

    def test_importa_um_lote(self):
        resp = self.client_api.post(
            "/api/v1/notas-fiscais/importar/",
            {"arquivos": [self._arquivo("nfe_proc.xml", "a.xml"),
                          self._arquivo("nfe_cru.xml", "b.xml")]},
            format="multipart",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["importadas"]), 2)
        self.assertEqual(resp.data["recusadas"], [])

    def test_um_arquivo_ruim_nao_derruba_o_lote(self):
        """Quem baixa o mes inteiro do e-mail traz anexo errado junto."""
        resp = self.client_api.post(
            "/api/v1/notas-fiscais/importar/",
            {"arquivos": [self._arquivo("nfe_proc.xml", "boa.xml"),
                          self._arquivo("nao_e_nfe.xml", "ruim.xml")]},
            format="multipart",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["importadas"]), 1)
        self.assertEqual(len(resp.data["recusadas"]), 1)
        self.assertEqual(resp.data["recusadas"][0]["arquivo"], "ruim.xml")
        self.assertIn("nota fiscal", resp.data["recusadas"][0]["motivo"])

    def test_lista_so_as_notas_da_propria_conta(self):
        from app.models import Conta, Fornecedor, NotaFiscal

        from .fabricas import criar_loja, lista

        self.client_api.post(
            "/api/v1/notas-fiscais/importar/",
            {"arquivos": [self._arquivo("nfe_proc.xml")]},
            format="multipart",
        )

        outra = Conta.objects.create(nome="Outro negocio", modulo_notas_ativo=True)
        NotaFiscal.objects.create(
            conta=outra,
            loja=criar_loja(
                conta=outra, nome_loja="Alheia", cidade="X", endereco="Y",
                cnpj="11111111000191",
            ),
            fornecedor=Fornecedor.objects.create(
                conta=outra, cnpj="22222222000191", razao_social="Alheio"
            ),
            chave="9" * 44,
            numero="77",
            serie="1",
            data_emissao=datetime.date(2026, 7, 1),
            valor_total=Decimal("1.00"),
            xml_bruto="<NFe/>",
        )

        resp = self.client_api.get("/api/v1/notas-fiscais/")
        numeros = {n["numero"] for n in lista(resp)}
        self.assertEqual(numeros, {"1234"})

    def test_filtra_as_nao_classificadas(self):
        """E a fila de trabalho: quem entra na tela quer ver o que falta."""
        from .fabricas import lista

        self.client_api.post(
            "/api/v1/notas-fiscais/importar/",
            {"arquivos": [self._arquivo("nfe_proc.xml")]},
            format="multipart",
        )

        resp = self.client_api.get("/api/v1/notas-fiscais/?classificada=false")
        self.assertEqual(len(lista(resp)), 1)

        resp = self.client_api.get("/api/v1/notas-fiscais/?classificada=true")
        self.assertEqual(len(lista(resp)), 0)

    def test_conta_sem_o_modulo_ligado_nao_alcanca_a_api(self):
        self.conta.modulo_notas_ativo = False
        self.conta.save()

        resp = self.client_api.get("/api/v1/notas-fiscais/")
        self.assertEqual(resp.status_code, 403)

    # --- Correcoes 2 e 3: parametro de filtro invalido e 400, nao 500 -----

    def test_parametro_de_invalido_retorna_400_com_mensagem_exata(self):
        """'ontem' vira ValidationError dentro do filter e derrubaria a API
        com 500 se nao fosse validado antes de chegar no queryset."""
        resp = self.client_api.get("/api/v1/notas-fiscais/?de=ontem")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data,
            {"error": "O parametro 'de' deve ser uma data no formato AAAA-MM-DD."},
        )

    def test_parametro_ate_invalido_retorna_400_com_mensagem_exata(self):
        resp = self.client_api.get("/api/v1/notas-fiscais/?ate=31/12/2026")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data,
            {"error": "O parametro 'ate' deve ser uma data no formato AAAA-MM-DD."},
        )

    def test_parametro_loja_com_uuid_invalido_retorna_400_com_mensagem_exata(self):
        resp = self.client_api.get("/api/v1/notas-fiscais/?loja=nao-e-um-uuid")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data,
            {"error": "O parametro 'loja' deve ser um UUID valido."},
        )

    def test_loja_com_uuid_valido_mas_inexistente_nao_da_erro(self):
        """Um UUID bem formado que nao casa com loja nenhuma e resultado
        vazio, nao 400 — a validacao e so de formato."""
        import uuid

        resp = self.client_api.get(f"/api/v1/notas-fiscais/?loja={uuid.uuid4()}")

        self.assertEqual(resp.status_code, 200)

    # --- Correcao 4: o lote tem teto de arquivos por requisicao -----------

    def test_lote_com_mais_de_500_arquivos_retorna_400_com_mensagem_exata(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        arquivos = [
            SimpleUploadedFile(f"a{i}.xml", b"<x/>", content_type="text/xml")
            for i in range(501)
        ]

        resp = self.client_api.post(
            "/api/v1/notas-fiscais/importar/",
            {"arquivos": arquivos},
            format="multipart",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.data,
            {"error": "Envie no maximo 500 arquivos por vez. Este lote tem 501."},
        )

    # --- Achado 1 do fix round 1: regressao ponta a ponta pelo endpoint ---

    def test_lote_com_bomba_disfarcada_em_utf16_e_recusada_sem_derrubar_o_lote(self):
        """Foi exatamente a falta desta regressao end-to-end que deixou o
        bypass passar: o teste unitario em ASCII literal dava confianca
        falsa, porque nunca exercitava o caminho real do upload (decode na
        view + parser)."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        bomba_disfarcada = xml_de("bomba_de_entidades.xml").encode("utf-16-le")

        resp = self.client_api.post(
            "/api/v1/notas-fiscais/importar/",
            {"arquivos": [
                self._arquivo("nfe_proc.xml", "boa.xml"),
                SimpleUploadedFile(
                    "bomba.xml", bomba_disfarcada, content_type="text/xml"
                ),
            ]},
            format="multipart",
        )

        # 200 com o lote misto: a bomba disfarcada e uma recusa de arquivo,
        # nao um erro de requisicao, e o resto do lote entra normalmente.
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["importadas"]), 1)
        self.assertEqual(len(resp.data["recusadas"]), 1)
        self.assertEqual(resp.data["recusadas"][0]["arquivo"], "bomba.xml")
        # Motivo exato e nao so len(recusadas) == 1: sem isso o teste passa
        # tambem contra a view antiga com errors="replace" e o parser antigo
        # baseado em regex — os dois ainda recusavam este arquivo, so que
        # pela razao errada (sem infNFe), depois de ja ter expandido a
        # bomba. E "DOCTYPE" e nao "nao e um XML valido" porque e essa
        # mensagem que prova que quem barrou foi o handler de DOCTYPE, e nao
        # uma checagem de codificacao julgando um documento diferente do que
        # o parser de verdade veria.
        self.assertEqual(
            resp.data["recusadas"][0]["motivo"],
            "O arquivo tem uma declaracao DOCTYPE, que uma NFe nunca tem.",
        )

        # Nenhuma nota entrou pela bomba: so a boa esta no banco.
        from app.models import NotaFiscal

        self.assertEqual(NotaFiscal.objects.count(), 1)

    def test_arquivo_que_nao_e_utf8_e_recusado_sem_derrubar_o_lote(self):
        """A camada que este teste prende e o `decode("utf-8")` ESTRITO da
        view, que ate agora nao tinha teste nenhum.

        Antes ele era `errors="replace"`, e isso corrompia em silencio: um
        XML em UTF-16 com BOM virava uma string de caracteres de substituicao
        que ainda parecia texto, seguia para o parser e era recusada por
        outro motivo qualquer — ou, pior, num arquivo so com um byte torto,
        entrava no banco com o nome do fornecedor estropiado e ninguem
        percebia. A NFe da SEFAZ e sempre UTF-8, entao o que nao decodifica
        assim e recusa daquele arquivo, com mensagem que a gerente entende, e
        o resto do lote entra normalmente.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        from app.models import NotaFiscal

        # UTF-16 COM BOM: e o que o Bloco de Notas do Windows gera em
        # "Salvar como UTF-16", e o BOM (\xff\xfe) e justamente o que nao
        # existe como sequencia UTF-8 valida — o decode estrito falha aqui.
        nao_utf8 = xml_de("nfe_cru.xml").encode("utf-16")

        resp = self.client_api.post(
            "/api/v1/notas-fiscais/importar/",
            {"arquivos": [
                self._arquivo("nfe_proc.xml", "boa.xml"),
                SimpleUploadedFile(
                    "torta.xml", nao_utf8, content_type="text/xml"
                ),
            ]},
            format="multipart",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["importadas"]), 1)
        self.assertEqual(len(resp.data["recusadas"]), 1)
        self.assertEqual(resp.data["recusadas"][0]["arquivo"], "torta.xml")
        # Mensagem exata: com errors="replace" o arquivo tambem seria
        # recusado, so que pelo parser e pela razao errada. So esta mensagem
        # prova que a recusa veio do decode estrito.
        self.assertEqual(
            resp.data["recusadas"][0]["motivo"],
            "O arquivo nao esta em UTF-8, que e a codificacao da nota fiscal "
            "eletronica.",
        )
        # O lote grava so a boa: nada do arquivo torto entra no banco.
        self.assertEqual(NotaFiscal.objects.count(), 1)

    # --- Achado 2 do fix round 1: a nota e prova, PATCH nao reescreve ela -

    def test_patch_nao_altera_numero_serie_data_ou_valor_no_banco(self):
        """A nota e a prova da despesa: numero, serie, data e valor vem do
        XML importado, e um PATCH nao pode fazer o relatorio discordar da
        prova (xml_bruto) guardada junto.

        A classificacao reabriu o PUT/PATCH — por isso o esperado aqui virou
        200, e nao mais o 405 de quando o ViewSet nao tinha
        UpdateModelMixin. O 200 e justamente o cenario perigoso: a porta
        esta aberta, e este teste afirma NO BANCO que os quatro campos de
        prova atravessaram a escrita sem mudar.
        """
        from app.models import ElementoDeDespesa, NotaFiscal

        resp = self.client_api.post(
            "/api/v1/notas-fiscais/importar/",
            {"arquivos": [self._arquivo("nfe_proc.xml")]},
            format="multipart",
        )
        nota_id = resp.data["importadas"][0]["id"]
        nota = NotaFiscal.objects.get(public_id=nota_id)
        valor_original = nota.valor_total
        numero_original = nota.numero
        serie_original = nota.serie
        data_original = nota.data_emissao

        # Junto de um `elemento` valido de proposito: sem ele o corpo ficaria
        # vazio para o serializer, e o teste passaria por nao ter nada a
        # escrever, e nao por os campos de prova estarem presos.
        elemento = ElementoDeDespesa.objects.get(
            nome="Embalagem", grupo__conta=self.conta
        )

        patch_resp = self.client_api.patch(
            f"/api/v1/notas-fiscais/{nota_id}/",
            {
                "elemento": str(elemento.public_id),
                "valor_total": "999999.99",
                "numero": "00000",
                "serie": "9",
                "data_emissao": "2000-01-01",
            },
            format="json",
        )

        self.assertEqual(patch_resp.status_code, 200)

        def conferir_a_prova(depois_de):
            """Afirma no banco, e nao so na resposta: a resposta e serializada
            dos mesmos campos read_only, entao ela mostraria o valor certo
            mesmo se o banco tivesse sido reescrito.
            """
            nota.refresh_from_db()
            self.assertEqual(nota.valor_total, valor_original, depois_de)
            self.assertEqual(nota.numero, numero_original, depois_de)
            self.assertEqual(nota.serie, serie_original, depois_de)
            self.assertEqual(nota.data_emissao, data_original, depois_de)

        # Conferido JA aqui, e nao so no fim: se o PATCH tivesse gravado a
        # prova, uma falha posterior no PUT terminaria o teste antes de a
        # conferencia acontecer, e o vazamento do PATCH passaria batido.
        conferir_a_prova("depois do PATCH")
        # A escrita que ERA para acontecer aconteceu — senao o 200 acima
        # estaria de novo comemorando um PATCH que nao gravou nada.
        self.assertEqual(nota.elemento, elemento)

        put_resp = self.client_api.put(
            f"/api/v1/notas-fiscais/{nota_id}/",
            {"valor_total": "888888.88", "numero": "11111"},
            format="json",
        )
        self.assertEqual(put_resp.status_code, 200)
        conferir_a_prova("depois do PUT")


class ClassificacaoTests(ApiDeNotasTests):
    """Herda o setUp: mesma conta, mesma loja, mesmo usuario logado."""

    def _importar_e_pegar_id(self, fixture="nfe_proc.xml"):
        resp = self.client_api.post(
            "/api/v1/notas-fiscais/importar/",
            {"arquivos": [self._arquivo(fixture)]},
            format="multipart",
        )
        return resp.data["importadas"][0]["id"]

    def _elemento(self, nome="Embalagem"):
        from app.models import ElementoDeDespesa

        return ElementoDeDespesa.objects.get(nome=nome, grupo__conta=self.conta)

    def test_classifica_a_nota(self):
        nota_id = self._importar_e_pegar_id()
        elemento = self._elemento()

        resp = self.client_api.patch(
            f"/api/v1/notas-fiscais/{nota_id}/",
            {"elemento": str(elemento.public_id)},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["elemento"]["nome"], "Embalagem")
        self.assertTrue(resp.data["classificada"])

    def test_classificar_ensina_o_fornecedor(self):
        """A proxima nota do mesmo fornecedor ja chega classificada."""
        from app.models import Fornecedor

        nota_id = self._importar_e_pegar_id()
        elemento = self._elemento()

        self.client_api.patch(
            f"/api/v1/notas-fiscais/{nota_id}/",
            {"elemento": str(elemento.public_id)},
            format="json",
        )

        fornecedor = Fornecedor.objects.get(cnpj="12345678000199", conta=self.conta)
        self.assertEqual(fornecedor.elemento_sugerido, elemento)

    def test_nao_aceita_elemento_de_outra_conta(self):
        """Senao o plano de contas de uma empresa vazaria para a nota da outra."""
        from app.models import Conta, ElementoDeDespesa, GrupoDeDespesa, NotaFiscal

        nota_id = self._importar_e_pegar_id()
        outra = Conta.objects.create(nome="Outro negocio")
        alheio = ElementoDeDespesa.objects.create(
            grupo=GrupoDeDespesa.objects.create(conta=outra, nome="Compras"),
            nome="Embalagem",
        )

        resp = self.client_api.patch(
            f"/api/v1/notas-fiscais/{nota_id}/",
            {"elemento": str(alheio.public_id)},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        # O status sozinho nao provaria que nada foi gravado antes da recusa:
        # a recusa tem que acontecer ANTES do save, e nao depois.
        nota = NotaFiscal.objects.get(public_id=nota_id)
        self.assertIsNone(nota.elemento)

    def test_classifica_a_nota_com_o_corpo_form_encoded(self):
        """O front manda JSON, mas a API publica aceita os dois formatos.

        Num corpo form-encoded o request.data e um QueryDict, e tirar dele o
        valor devolvia a LISTA em vez da string: o mesmo PATCH que gravava em
        JSON respondia 400 dizendo que "['<uuid>']" nao e um UUID.
        """
        from app.models import NotaFiscal

        nota_id = self._importar_e_pegar_id()
        elemento = self._elemento()

        resp = self.client_api.patch(
            f"/api/v1/notas-fiscais/{nota_id}/",
            {"elemento": str(elemento.public_id)},
            format="multipart",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        # Pelo banco, e nao so pela resposta: 200 com nada gravado e o pior
        # modo de falha desta tela.
        self.assertEqual(NotaFiscal.objects.get(public_id=nota_id).elemento, elemento)

    def test_elemento_inexistente_responde_sem_nome_interno_de_campo(self):
        """A recusa fala do elemento de despesa, nao de `elemento_id`.

        `elemento_id` e nome de dentro; a tela e o front so conhecem
        `elemento`. Vazar o outro nome manda a gerente procurar um campo que
        nao esta escrito em lugar nenhum — e a frase antiga ainda trazia
        "UUID" e o id recebido.
        """
        nota_id = self._importar_e_pegar_id()

        resp = self.client_api.patch(
            f"/api/v1/notas-fiscais/{nota_id}/",
            {"elemento": "nao-e-um-id"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertNotIn("elemento_id", str(resp.data))
        self.assertEqual(
            resp.data["elemento"][0], "O elemento de despesa escolhido nao existe."
        )

    def test_lista_o_plano_de_contas_da_conta(self):
        from .fabricas import lista

        self._importar_e_pegar_id()

        resp = self.client_api.get("/api/v1/grupos-de-despesa/")
        nomes = {g["nome"] for g in lista(resp)}
        self.assertEqual(nomes, {"Compras", "Operacional", "Pessoal", "Impostos"})

    def test_o_plano_nasce_ao_listar_mesmo_sem_nota(self):
        """Quem abre a tela antes de subir a primeira nota nao ve lista vazia."""
        from .fabricas import lista

        resp = self.client_api.get("/api/v1/grupos-de-despesa/")
        self.assertEqual(len(lista(resp)), 4)

    def test_lista_os_elementos_da_conta_sem_os_da_outra(self):
        """O seletor da tela de classificacao so pode oferecer o plano proprio."""
        from .fabricas import lista
        from app.models import Conta, ElementoDeDespesa, GrupoDeDespesa

        outra = Conta.objects.create(nome="Outro negocio")
        ElementoDeDespesa.objects.create(
            grupo=GrupoDeDespesa.objects.create(conta=outra, nome="Compras"),
            nome="Elemento alheio",
        )

        resp = self.client_api.get("/api/v1/elementos-de-despesa/")
        nomes = {e["nome"] for e in lista(resp)}

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("Elemento alheio", nomes)
        self.assertIn("Embalagem", nomes)

    def test_lista_os_grupos_da_conta_sem_os_da_outra(self):
        """O gemeo do teste de elementos, para os grupos.

        Sem ele o filtro por conta do GrupoDeDespesaViewSet podia ser apagado
        com a suite inteira verde: o teste vizinho compara os nomes num cenario
        de uma conta so, entao ele nunca ve o vazamento. Com o banco de mais de
        uma empresa, o plano de contas da vizinha apareceria no seletor.
        """
        from .fabricas import lista
        from app.models import Conta, GrupoDeDespesa

        outra = Conta.objects.create(nome="Outro negocio")
        GrupoDeDespesa.objects.create(conta=outra, nome="Grupo alheio")

        resp = self.client_api.get("/api/v1/grupos-de-despesa/")
        nomes = {g["nome"] for g in lista(resp)}

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("Grupo alheio", nomes)
        self.assertIn("Compras", nomes)

    # --- O plano de contas e editavel: criar elemento e criar grupo ------

    def test_cria_elemento_de_despesa(self):
        """Quando aparece uma despesa que nao cabe no plano inicial, a gerencia
        cria o elemento na tela — a rota tem que funcionar, e nao responder 500.
        """
        from app.models import ElementoDeDespesa, GrupoDeDespesa
        from app.services.plano_de_contas import garantir_plano_de_contas

        # O plano nasce sozinho na primeira listagem; aqui ele e semeado a mao
        # porque este teste nao passa pela tela de listagem antes de criar.
        garantir_plano_de_contas(self.conta)
        grupo = GrupoDeDespesa.objects.get(conta=self.conta, nome="Operacional")

        resp = self.client_api.post(
            "/api/v1/elementos-de-despesa/",
            {"nome": "Manutencao do forno", "grupo_id": str(grupo.public_id)},
            format="json",
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["grupo"], "Operacional")
        # No banco, e no grupo certo: a resposta sozinha nao provaria em que
        # grupo o elemento foi pendurado.
        elemento = ElementoDeDespesa.objects.get(nome="Manutencao do forno")
        self.assertEqual(elemento.grupo, grupo)

    def test_nao_cria_elemento_no_grupo_de_outra_conta(self):
        """Estar logado limita quais objetos a pessoa alcanca, nunca o que cabe
        no corpo do pedido: o id do grupo da empresa vizinha entra no JSON do
        mesmo jeito, e sem a guarda seria aceito.
        """
        from app.models import Conta, ElementoDeDespesa, GrupoDeDespesa

        outra = Conta.objects.create(nome="Outro negocio")
        alheio = GrupoDeDespesa.objects.create(conta=outra, nome="Compras")

        resp = self.client_api.post(
            "/api/v1/elementos-de-despesa/",
            {"nome": "Invasor", "grupo_id": str(alheio.public_id)},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        # A recusa nao pode deixar rastro: nem no grupo alheio, nem em lugar
        # nenhum.
        self.assertFalse(
            ElementoDeDespesa.objects.filter(nome="Invasor").exists()
        )

    def test_nao_cria_elemento_sem_grupo(self):
        """Sem grupo o banco recusa, e recusava com 500 e stack trace no log.
        Quem chamou merece 400 com uma frase que a gerencia entende.
        """
        from app.models import ElementoDeDespesa

        resp = self.client_api.post(
            "/api/v1/elementos-de-despesa/",
            {"nome": "Sem grupo"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        # A mensagem exata, e nao so o 400: sem o campo `grupo_id` obrigatorio
        # a recusa ainda sairia 400, mas pela regra de nome unico por grupo e
        # com o texto padrao do framework — verde pelo motivo errado, e com
        # uma frase que a gerencia da padaria nao entende.
        self.assertEqual(
            resp.data["grupo_id"][0],
            "Escolha o grupo de despesa deste elemento.",
        )
        self.assertFalse(
            ElementoDeDespesa.objects.filter(nome="Sem grupo").exists()
        )

    def test_superuser_nao_cria_grupo_sem_empresa(self):
        """O dono da plataforma nao pertence a empresa nenhuma: para ele a
        conta do pedido e None, e gravar um grupo sem conta estourava 500.
        Ele precisa dizer de qual empresa e o grupo, e a API ainda nao oferece
        esse caminho — entao recusa explicando, em vez de quebrar.
        """
        from app.models import GrupoDeDespesa

        dono = User.objects.create_superuser(
            username="dono@x.com", password="123456"
        )
        client_dono = APIClient()
        client_dono.force_authenticate(dono)

        resp = client_dono.post(
            "/api/v1/grupos-de-despesa/",
            {"nome": "Grupo sem dono"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("empresa", str(resp.data).lower())
        self.assertFalse(
            GrupoDeDespesa.objects.filter(nome="Grupo sem dono").exists()
        )

    def test_nao_cria_grupo_com_nome_repetido_na_empresa(self):
        """Criar "Operacional" duas vezes e o erro obvio de quem nao lembra se
        ja criou. Sem guarda, a restricao do banco sobe crua e vira 500: a tela
        mostra "erro inesperado" e a gerencia tenta de novo, com o mesmo nome.
        """
        from app.models import GrupoDeDespesa

        self.client_api.post(
            "/api/v1/grupos-de-despesa/", {"nome": "Insumos"}, format="json"
        )

        resp = self.client_api.post(
            "/api/v1/grupos-de-despesa/", {"nome": "Insumos"}, format="json"
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("ja existe", str(resp.data).lower())
        self.assertEqual(
            GrupoDeDespesa.objects.filter(conta=self.conta, nome="Insumos").count(), 1
        )

    def test_a_empresa_vizinha_pode_ter_um_grupo_com_o_mesmo_nome(self):
        """O nome e unico DENTRO da empresa, e nao no sistema: "Insumos" e o
        nome obvio, e duas padarias diferentes vao usar o mesmo.
        """
        from app.models import Conta, GrupoDeDespesa

        vizinha = Conta.objects.create(nome="Aurora Salgados", modulo_notas_ativo=True)
        GrupoDeDespesa.objects.create(conta=vizinha, nome="Insumos")

        resp = self.client_api.post(
            "/api/v1/grupos-de-despesa/", {"nome": "Insumos"}, format="json"
        )

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(GrupoDeDespesa.objects.filter(nome="Insumos").count(), 2)

    def test_nao_renomeia_grupo_para_um_nome_que_ja_existe(self):
        """A mesma colisao pela outra porta: em vez de criar repetido, renomear
        para o que ja esta la.
        """
        from app.models import GrupoDeDespesa

        self.client_api.post(
            "/api/v1/grupos-de-despesa/", {"nome": "Insumos"}, format="json"
        )
        outro = GrupoDeDespesa.objects.create(conta=self.conta, nome="Embalagens")

        resp = self.client_api.patch(
            f"/api/v1/grupos-de-despesa/{outro.public_id}/",
            {"nome": "Insumos"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("ja existe", str(resp.data).lower())
        outro.refresh_from_db()
        self.assertEqual(outro.nome, "Embalagens")

    def test_salvar_o_grupo_sem_trocar_o_nome_nao_e_colisao(self):
        """Reenviar o mesmo nome acontece o tempo todo: a tela manda o
        formulario inteiro quando so o `ativo` mudou. Se o proprio grupo
        contasse como irmao, ele colidiria consigo mesmo.
        """
        from app.models import GrupoDeDespesa

        grupo = GrupoDeDespesa.objects.create(conta=self.conta, nome="Insumos")

        resp = self.client_api.patch(
            f"/api/v1/grupos-de-despesa/{grupo.public_id}/",
            {"nome": "Insumos", "ativo": False},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        grupo.refresh_from_db()
        self.assertFalse(grupo.ativo)

    # --- Desclassificar: lancar no elemento errado e operacao normal ----

    def test_limpar_a_classificacao_nao_apaga_o_que_o_fornecedor_ja_sabia(self):
        """Quem lancou a nota no elemento errado precisa poder desfazer.

        E desfazer na nota NAO desaprende o fornecedor: a sugestao dele e o que
        poupa a digitacao das proximas notas, e uma correcao pontual nao e
        motivo para jogar fora o que ja se sabia.
        """
        from app.models import Fornecedor, NotaFiscal

        nota_id = self._importar_e_pegar_id()
        elemento = self._elemento()

        self.client_api.patch(
            f"/api/v1/notas-fiscais/{nota_id}/",
            {"elemento": str(elemento.public_id)},
            format="json",
        )

        resp = self.client_api.patch(
            f"/api/v1/notas-fiscais/{nota_id}/",
            {"elemento": None},
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(resp.data["classificada"])
        # No banco, e nao so na resposta: e o banco que alimenta o relatorio.
        nota = NotaFiscal.objects.get(public_id=nota_id)
        self.assertIsNone(nota.elemento)

        fornecedor = Fornecedor.objects.get(cnpj="12345678000199", conta=self.conta)
        self.assertEqual(fornecedor.elemento_sugerido, elemento)

    def test_nao_move_elemento_para_grupo_de_outra_conta(self):
        """A mesma guarda vale na edicao, e nao so na criacao.

        O `grupo_id` gravavel tambem chega pelo PATCH: o get_queryset impede
        alcancar o elemento da vizinha, mas nao impediria mandar um elemento
        proprio para o grupo dela — e o elemento nasceria fora do plano de
        contas da empresa, sumindo do seletor de quem o criou.
        """
        from app.models import Conta, ElementoDeDespesa, GrupoDeDespesa
        from app.services.plano_de_contas import garantir_plano_de_contas

        garantir_plano_de_contas(self.conta)
        meu = ElementoDeDespesa.objects.filter(grupo__conta=self.conta).first()
        grupo_de_origem = meu.grupo

        outra = Conta.objects.create(nome="Outro negocio")
        alheio = GrupoDeDespesa.objects.create(conta=outra, nome="Compras")

        resp = self.client_api.patch(
            f"/api/v1/elementos-de-despesa/{meu.public_id}/",
            {"grupo_id": str(alheio.public_id)},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        meu.refresh_from_db()
        self.assertEqual(meu.grupo, grupo_de_origem)


class ModuloNoUsuarioLogadoTests(TestCase):
    """A flag do modulo chega ao front pelo /user/me/.

    Antes disso a tela adivinhava: chamava uma rota do modulo e tratava a
    recusa como "nao tem". Adivinhar custava caro — erro de rede virava "sem
    modulo", a sonda rodava em toda carga de painel, e ela ainda batia num
    caminho de ESCRITA no servidor (a semeadura do plano de contas) so para
    responder uma pergunta de leitura.
    """

    def setUp(self):
        from django.contrib.auth.models import Group, User
        from rest_framework.test import APIClient

        from app.models import Conta

        from .fabricas import vincular_conta

        for nome in ("Admin", "Gerente", "Responsavel"):
            Group.objects.get_or_create(name=nome)

        self.conta = Conta.objects.create(nome="Marina")
        self.user = User.objects.create_user(username="g@x.com", password="123456")
        self.user.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(self.user, self.conta)

        self.client_api = APIClient()
        self.client_api.force_authenticate(self.user)

    def test_diz_que_o_modulo_esta_desligado(self):
        """Desligado e o padrao: a empresa que nao contratou nao ve a aba."""
        resp = self.client_api.get("/api/v1/user/me/")

        self.assertEqual(resp.status_code, 200)
        self.assertIs(resp.data["modulos"]["notas_fiscais"], False)

    def test_diz_que_o_modulo_esta_ligado(self):
        self.conta.modulo_notas_ativo = True
        self.conta.save()

        resp = self.client_api.get("/api/v1/user/me/")

        self.assertIs(resp.data["modulos"]["notas_fiscais"], True)

    def test_o_dono_da_plataforma_ve_o_modulo_mesmo_sem_empresa(self):
        """O superuser nao tem empresa vinculada: para ele a funcao de conta
        devolve None, e a resposta nao pode estourar.

        E tem que dizer "sim": ModuloDeNotasAtivo libera o superuser de
        proposito, entao ele abre as rotas do modulo com 200. Dizer "nao" aqui
        fazia esta rota contradizer a permissao na mesma sessao, e sumia com a
        aba justamente para quem confere o modulo de fora da empresa.
        """
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient

        dono = User.objects.create_superuser(username="dono@x.com", password="123456")
        client_dono = APIClient()
        client_dono.force_authenticate(dono)

        resp = client_dono.get("/api/v1/user/me/")

        self.assertEqual(resp.status_code, 200)
        self.assertIs(resp.data["modulos"]["notas_fiscais"], True)
        # A outra metade do acordo: a rota do modulo responde 200 para o mesmo
        # login. Sao as duas respostas que precisam concordar.
        self.assertEqual(
            client_dono.get("/api/v1/notas-fiscais/").status_code, 200
        )


class TotaisDaListagemDeNotasTests(TestCase):
    """A faixa de totais da tela de notas.

    A listagem pagina em 50, entao a tela NAO pode somar o que esta na pagina:
    num mes de 300 notas ela mostraria um terco do gasto com cara de total, e
    esse e o numero pelo qual a gerencia fecha o mes. Por isso a soma sai do
    servidor, sobre o filtro inteiro, e viaja junto da propria pagina.
    """

    def setUp(self):
        Group.objects.get_or_create(name="Gerente")

        self.conta = conta_padrao()
        self.conta.modulo_notas_ativo = True
        self.conta.save()

        self.loja = criar_loja(
            nome_loja="Loja A", cidade="Patos", endereco="Rua 1",
            cnpj="98765432000188",
        )
        self.outra_loja = criar_loja(
            nome_loja="Loja B", cidade="Patos", endereco="Rua 2",
            cnpj="98765432000269",
        )

        self.user = User.objects.create_user(username="ger@t.com", password="123456")
        self.user.groups.add(Group.objects.get(name="Gerente"))
        vincular_conta(self.user, self.conta)

        self.client_api = APIClient()
        self.client_api.force_authenticate(self.user)

        self.proxima_chave = 0

    def _fornecedor(self, conta=None, cnpj="12345678000199"):
        from app.models import Fornecedor

        fornecedor, _ = Fornecedor.objects.get_or_create(
            conta=conta or self.conta,
            cnpj=cnpj,
            defaults={"razao_social": "Fornecedor X"},
        )
        return fornecedor

    def _elemento(self, conta=None, nome="Embalagem"):
        from app.models import ElementoDeDespesa, GrupoDeDespesa

        conta = conta or self.conta
        grupo, _ = GrupoDeDespesa.objects.get_or_create(conta=conta, nome="Insumos")
        elemento, _ = ElementoDeDespesa.objects.get_or_create(grupo=grupo, nome=nome)
        return elemento

    def _nota(self, valor, data="2026-08-05", loja=None, conta=None, elemento=None):
        from app.models import NotaFiscal

        self.proxima_chave += 1
        conta = conta or self.conta
        return NotaFiscal.objects.create(
            conta=conta,
            loja=loja or self.loja,
            fornecedor=self._fornecedor(conta=conta, cnpj="12345678000199"
                                        if conta == self.conta else "22222222000191"),
            chave=str(self.proxima_chave).zfill(44),
            numero=str(self.proxima_chave),
            serie="1",
            data_emissao=datetime.date.fromisoformat(data),
            valor_total=Decimal(valor),
            elemento=elemento,
            xml_bruto="<NFe/>",
        )

    def test_soma_o_valor_de_todas_as_notas_da_conta(self):
        self._nota("100.50")
        self._nota("200.25")

        resp = self.client_api.get("/api/v1/notas-fiscais/")

        self.assertEqual(resp.data["totais"]["valor"], "300.75")

    def test_o_total_nao_inclui_nota_de_outra_empresa(self):
        """Mesma regra da listagem: o total e o gasto DESTA empresa."""
        from app.models import Conta

        self._nota("100.00")
        outra = Conta.objects.create(nome="Outro negocio", modulo_notas_ativo=True)
        self._nota(
            "999.00",
            conta=outra,
            loja=criar_loja(
                conta=outra, nome_loja="Alheia", cidade="X", endereco="Y",
                cnpj="11111111000191",
            ),
        )

        resp = self.client_api.get("/api/v1/notas-fiscais/")

        self.assertEqual(resp.data["totais"]["valor"], "100.00")

    def test_o_total_obedece_o_filtro_de_loja_e_de_data(self):
        """Senao a faixa diria um numero e a tabela embaixo mostraria outro."""
        self._nota("100.00", data="2026-08-05", loja=self.loja)
        self._nota("50.00", data="2026-08-05", loja=self.outra_loja)
        self._nota("70.00", data="2026-09-01", loja=self.loja)

        resp = self.client_api.get(
            f"/api/v1/notas-fiscais/?loja={self.loja.public_id}"
            "&de=2026-08-01&ate=2026-08-31"
        )

        self.assertEqual(resp.data["totais"]["valor"], "100.00")

    def test_conta_e_soma_o_que_falta_classificar(self):
        self._nota("100.00")
        self._nota("30.00")
        self._nota("70.00", elemento=self._elemento())

        resp = self.client_api.get("/api/v1/notas-fiscais/")

        self.assertEqual(resp.data["totais"]["pendentes"]["quantidade"], 2)
        self.assertEqual(resp.data["totais"]["pendentes"]["valor"], "130.00")

    def test_sem_pendencia_os_numeros_sao_zero_e_nao_nulos(self):
        """`Sum` de conjunto vazio e None, e a tela nao pode receber nulo onde
        espera dinheiro: vira "R$ NaN" na faixa."""
        self._nota("100.00", elemento=self._elemento())

        resp = self.client_api.get("/api/v1/notas-fiscais/")

        self.assertEqual(resp.data["totais"]["pendentes"]["quantidade"], 0)
        self.assertEqual(resp.data["totais"]["pendentes"]["valor"], "0.00")

    def test_sem_nota_nenhuma_o_total_e_zero(self):
        resp = self.client_api.get("/api/v1/notas-fiscais/")

        self.assertEqual(resp.data["count"], 0)
        self.assertEqual(resp.data["totais"]["valor"], "0.00")

    def test_a_segunda_pagina_traz_o_mesmo_total_da_primeira(self):
        """O total e do filtro, nao da pagina — e e essa a razao de existir."""
        for _ in range(51):
            self._nota("10.00")

        primeira = self.client_api.get("/api/v1/notas-fiscais/")
        segunda = self.client_api.get("/api/v1/notas-fiscais/?page=2")

        self.assertEqual(len(primeira.data["results"]), 50)
        self.assertEqual(len(segunda.data["results"]), 1)
        self.assertEqual(primeira.data["totais"]["valor"], "510.00")
        self.assertEqual(segunda.data["totais"]["valor"], "510.00")

    def test_o_filtro_de_classificada_nao_deixa_pendencia_fantasma(self):
        """Com "so as classificadas" na tela, nao ha o que falta classificar:
        a faixa nao pode avisar de uma pendencia que nao esta na lista."""
        self._nota("100.00")
        self._nota("70.00", elemento=self._elemento())

        resp = self.client_api.get("/api/v1/notas-fiscais/?classificada=true")

        self.assertEqual(resp.data["totais"]["valor"], "70.00")
        self.assertEqual(resp.data["totais"]["pendentes"]["quantidade"], 0)
        self.assertEqual(resp.data["totais"]["pendentes"]["valor"], "0.00")
