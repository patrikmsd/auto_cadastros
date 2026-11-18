"""Assistente de preenchimento: servidor local (127.0.0.1:8780) + extensão do Chrome (pasta extensao/).

1. na página http://127.0.0.1:8780 você digita o código do card (ou a placa) e ele entra na fila
2. o robô confere o card como o lote (SÓ LEITURA: OCR, fotos pela IA, FIPE oficial, tabela, regras), sem preencher nada
3. monta o resultado CAMPO A CAMPO: o que está no card × o que foi encontrado, a fonte e se bate
4. a extensão, quando você abre esse card no PowerCRM, mostra um selo ao lado de cada campo e um painel lateral.
   O valor sugerido aparece "apagado" no campo vazio; "preencher" (ou "Preencher tudo") só digita na tela: quem confere e salva é o analista.
   O analista pode marcar o que está errado e deixar uma observação (saida/assistente/observacoes.csv).
   Nome, CPF, categoria da CNH e sexo: a IA (luna) olha a CNH e a selfie do associado (uma chamada, custo registrado,
   cache em pessoa_ia.json). O código FIPE não tem botão: o robô só sugere.

Uso:  python -m robo.assistente                      (servidor + página)
      python -m robo.assistente --importar robo/saida/20261030-100606   (usa uma demo já rodada, sem abrir o CRM)
O demo e o lote continuam iguais: este script só reaproveita as peças deles.
"""
import argparse
import csv
import json
import queue
import re
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __package__ in (None, ""):  # rodando como arquivo (py assistente.py): vira pacote para os imports relativos
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "robo"

from . import config, custo_ia, decidir_tabela, gpt, incertezas, regras, validar_docs
from .conferir import NOMES

PORTA = 8780
VERSAO = 5  # sobe quando a API muda: a página (lida do disco a cada acesso) avisa se o servidor aberto é mais antigo
PASTA = config.PASTA_SAIDA / "assistente"
INDICE = PASTA / "indice.json"
OBSERVACOES = PASTA / "observacoes.csv"  # todas as observações dos analistas (acumula; uma linha por envio)
CABECALHO = "X-Assistente"  # toda chamada da API exige: página de outro site não consegue mandar sem a permissão (CORS)
CHAVE = "X-Chave"  # chave do analista: obrigatória para quem chega pela rede (o próprio PC do servidor dispensa)
ANALISTAS = PASTA / "analistas.json"  # chave → nome do analista (python -m robo.assistente --nova-chave "Nome")
VAZIOS = ("", "Selecione", "Não Informado", None)
PERGUNTA_DOCS = """Cadastro de proteção veicular. Você recebe {n} imagens, nesta ordem (o rótulo é onde a foto foi enviada, que pode estar ERRADO):
{ordem}
Para CADA imagem diga o que ela realmente mostra e só leia campos de documento que estejam de fato na imagem. NÃO invente:
o que não estiver legível na própria imagem fica "". Uma foto de carro não é CNH, nem CRLV, nem comprovante.
Responda só JSON: {"imagens": [{"n": 1, "tipo": "cnh|crlv|comprovante|selfie_com_pessoa|veiculo|outro", "legivel": true,
  "cnh": {"nome_completo": "", "cpf": "", "rg": "", "orgao_expedidor": "", "data_emissao": "", "data_nascimento": "", "validade": "",
          "primeira_habilitacao": "", "registro": "", "categoria": ""},
  "crlv": {"placa": "", "marca": "", "modelo": "", "ano_fabricacao": "", "ano_modelo": "", "cor": "", "especie_tipo": "",
           "municipio": "", "uf": "", "combustivel": ""},
  "comprovante": {"nome_titular": "", "logradouro": "", "numero": "", "bairro": "", "cidade": "", "uf": "", "cep": ""},
  "pessoa_visivel": false, "sexo_aparente": "masculino|feminino|nao_da_para_saber", "telefones": [""]}],
 "observacao": ""}
Preencha "cnh" só se tipo = cnh, "crlv" só se tipo = crlv, "comprovante" só se tipo = comprovante (senão deixe {}).
"pessoa_visivel": true só se há o rosto de uma pessoa na imagem; "sexo_aparente" só nesse caso (senão "nao_da_para_saber").
Datas em dd/mm/aaaa. Categoria: só as letras do campo 9 CAT (ex.: AB). Órgão expedidor como "SSP GO"."""

VERSAO_DOCS = 3  # muda quando a pergunta muda: o cache antigo é refeito
DOCUMENTOS = re.compile(r"documento|cnh|crlv|comprovante|selfie|anexo", re.I)
NAO_DOCUMENTO = re.compile(r"chassi|assinatura|autoriza", re.I)
# tipo do veículo (CRLV/fotos) → opção do CRM
TIPO_CRM = [(r"MOTO|CICLOMOTOR|MOTONETA", "Moto"), (r"CAMINH|ONIBUS|MICRO|CARGA|TRATOR", "Caminhão ou micro-ônibus"),
            (r"AUTOMOVEL|CAMIONETA|CAMINHONETE|UTILIT|PASSAGEIRO|CARRO|PICK|VAN|MISTO", "Carro ou utilitário pequeno")]
CORES_CRM = ["Preto", "Branca", "Azul", "Vermelho", "Verde", "Cinza", "Bege", "Amarelo", "Prata", "Dourado", "Laranja", "Marrom",
             "Fantasia", "Roxo", "Rosa"]
# item da conferência → campo da tela em que ele aparece (aba Cotações)
ITEM_CAMPO = {"combustivel": ("vhclFuel", "Combustível"), "cambio": ("vhclShft", "Câmbio"),
              "valor_protegido": ("vhclFipeProtectedVl", "Valor protegido"), "dia_vencimento": ("vhclExprtnDay", "Dia de Vencimento"),
              "tabela": ("qttnPlansTableSelect", "Tabela"), "fipe": ("vhclFipeCode", "Código FIPE")}
# documento do CRLV × campo já preenchido no card
CRLV_CAMPO = {"placa": ("vhclPlates", "Placa"), "chassi": ("vhclChassi", "Chassi"), "renavam": ("vhclRenavam", "Renavam")}
SITUACAO_CAMPO = {"OK": "ok", "DIVERGENTE": "diverge", "ALERTA": "conferir", "NAO_VERIFICAVEL": "conferir"}

TRABALHOS = {}  # código → situação do trabalho (fila, rodando, pronto, erro, pulado)
FILA = queue.Queue()
_trava = threading.Lock()


def _gravar_indice():
    PASTA.mkdir(parents=True, exist_ok=True)
    INDICE.write_text(json.dumps(TRABALHOS, ensure_ascii=False, indent=1), encoding="utf-8")


def _atualizar(pedido, **dados):
    with _trava:
        TRABALHOS[pedido] = {**TRABALHOS.get(pedido, {}), **dados}
        _gravar_indice()


def documentos_ia(pasta_card, imagens):
    """Uma chamada da luna com TODAS as fotos que podem ser documento (vistoria + anexos das abas Atividades, Cotações e
    Contato): ela diz o que
    cada uma realmente é e lê só o que está nela. Grava em documentos_ia.json e não pergunta de novo."""
    # pelo espaço em que foi enviada OU porque a análise por foto viu um documento nela (CNH no espaço das chaves)
    vistas = _ler_json(pasta_card / "analise_fotos.json").get("fotos") or {}
    rotulos = [rot for rot in imagens if (DOCUMENTOS.search(rot) and not NAO_DOCUMENTO.search(rot))
               or (vistas.get(rot) or {}).get("tipo_real") in ("cnh", "crlv", "comprovante", "selfie")][:16]
    if not rotulos:
        return None
    cache = pasta_card / "documentos_ia.json"
    if cache.exists():
        dados = json.loads(cache.read_text(encoding="utf-8"))
        if dados.get("_versao") == VERSAO_DOCS and dados.get("_rotulos") == rotulos:
            return dados
    ordem = "\n".join(f"{i}) {rot}" for i, rot in enumerate(rotulos, 1))
    resposta = gpt.perguntar_gpt("Documentos · o que cada foto é + leitura", PERGUNTA_DOCS.replace("{n}", str(len(rotulos)))
                                 .replace("{ordem}", ordem), [imagens[r] for r in rotulos], modelo=gpt.MODELO, esforco="low")
    for item in resposta.get("imagens") or []:
        n = item.get("n")
        item["rotulo"] = rotulos[n - 1] if isinstance(n, int) and 0 < n <= len(rotulos) else ""
    resposta.update(_rotulos=rotulos, _versao=VERSAO_DOCS)
    cache.write_text(json.dumps(resposta, ensure_ascii=False, indent=1), encoding="utf-8")
    return resposta


def escolher_documentos(bruto, card, atuais):
    """Qual imagem vale para cada documento: CNH com o nome/CPF do card (ou a marcada "correta"), CRLV com a placa do card,
    comprovante legível, selfie só com pessoa visível. Devolve os campos lidos + avisos (CNHs de pessoas diferentes etc.)."""
    itens = [i for i in (bruto or {}).get("imagens") or [] if i.get("rotulo")]
    so_digitos = lambda t: re.sub(r"\D", "", str(t or ""))
    nome_card, cpf_card = set(_palavras(atuais.get("clntFullName"))), so_digitos(atuais.get("clntRegistration"))
    avisos, fotos, saida = [], {}, {"cnh": {}, "crlv": {}, "comprovante": {}, "sexo": "", "telefones": []}

    def da_pessoa(cnh):
        # nome incompleto no card ("lucas"): vale se TODAS as palavras do card estão no nome da CNH (vira incerteza mais abaixo)
        nome = set(_palavras(cnh.get("nome_completo")))
        return (cpf_card and so_digitos(cnh.get("cpf")) == cpf_card) or (nome_card and (len(nome & nome_card) >= 2 or nome_card <= nome))

    def so_pelo_nome_parcial(cnh):
        nome = set(_palavras(cnh.get("nome_completo")))
        return not (cpf_card and so_digitos(cnh.get("cpf")) == cpf_card) and len(nome & nome_card) < 2 and bool(nome_card) and nome_card <= nome

    cnhs = [i for i in itens if i.get("tipo") == "cnh" and any((i.get("cnh") or {}).values())]
    if cnhs:
        cnhs.sort(key=lambda i: (bool(da_pessoa(i["cnh"])), "corret" in i["rotulo"].lower(), bool(i.get("legivel"))), reverse=True)
        saida["cnh"], fotos["CNH do associado"] = cnhs[0]["cnh"], cnhs[0]["rotulo"]
        nomes = {" ".join(_palavras(i["cnh"].get("nome_completo"))) for i in cnhs if i["cnh"].get("nome_completo")}
        if len(nomes) > 1:
            avisos.append(f"há CNHs de pessoas diferentes nos documentos ({'; '.join(sorted(nomes))}): usei a de '{cnhs[0]['rotulo']}'")
        if (nome_card or cpf_card) and not da_pessoa(cnhs[0]["cnh"]):
            saida["cnh_de_outra_pessoa"] = cnhs[0]["cnh"].get("nome_completo") or "?"
        elif so_pelo_nome_parcial(cnhs[0]["cnh"]):
            saida["cnh_pelo_nome_parcial"] = " ".join(sorted(nome_card)).title()
    placa = _so_letras(card.get("placa"))
    crlvs = [i for i in itens if i.get("tipo") == "crlv" and any((i.get("crlv") or {}).values())]
    crlvs.sort(key=lambda i: (_so_letras(i["crlv"].get("placa")) == placa, bool(i.get("legivel"))), reverse=True)
    if crlvs:
        if _so_letras(crlvs[0]["crlv"].get("placa")) not in ("", placa):
            avisos.append(f"o CRLV enviado é de outra placa ({crlvs[0]['crlv'].get('placa')}): não usado")
        else:
            saida["crlv"], fotos["CRLV do veículo"] = crlvs[0]["crlv"], crlvs[0]["rotulo"]
    comps = [i for i in itens if i.get("tipo") == "comprovante" and any((i.get("comprovante") or {}).values())]
    if comps:
        comps.sort(key=lambda i: bool(i.get("legivel")), reverse=True)
        saida["comprovante"], fotos["comprovante de endereço"] = comps[0]["comprovante"], comps[0]["rotulo"]
    pessoas = [i for i in itens if i.get("pessoa_visivel") and i.get("sexo_aparente") in ("masculino", "feminino")]
    if pessoas:
        pessoas.sort(key=lambda i: "selfie" in i["rotulo"].lower(), reverse=True)
        saida["sexo"], fotos["selfie do associado"] = pessoas[0]["sexo_aparente"], pessoas[0]["rotulo"]
    o_que = {"veiculo": "uma foto do veículo", "outro": "outra coisa (ou está ilegível)"}
    avisos += [f"foto no lugar errado: o espaço '{i['rotulo']}' não tem o documento, tem {o_que[i['tipo']]}" for i in itens
               if re.search(r"cnh|crlv|documento do ve|comprovante", i["rotulo"], re.I) and i.get("tipo") in o_que]
    saida["telefones"] = [(so_digitos(t), i["rotulo"]) for i in itens for t in i.get("telefones") or [] if len(so_digitos(t)) >= 10]
    saida.update(_fotos=fotos, avisos=avisos)
    return saida


def _ler_json(arquivo):
    return json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}


def _origem(f):
    """De onde a imagem veio no CRM: fotos da vistoria ou anexo (aba Atividades ou Cotações)."""
    if f.get("anexo") or re.search(r"anexo", f["rotulo"], re.I):
        return f"Anexo · aba {f.get('aba') or ('Cotações' if re.search(r'cota', f['rotulo'], re.I) else 'Atividades')}"
    return "Vistoria · espaço da foto"


def _palavras(t):
    return regras.sem_acento(str(t or "")).upper().split()


def _so_letras(t):
    return re.sub(r"\W", "", str(t or "")).upper()


# ---------------- resultado campo a campo ----------------

def montar(pasta_card, r, codigo, segundos=None, custo=None):
    """Junta conferência + propostas de preenchimento num resultado por campo da tela (o que a extensão mostra)."""
    from .demo import propor_preenchimento

    card, ocr, placa = r["card"], r.get("ocr") or {}, r["card"]["placa"]
    ler = lambda nome: json.loads((pasta_card / nome).read_text(encoding="utf-8")) if (pasta_card / nome).exists() else []
    atuais = {c["nome"]: c["valor"] for c in ler("card_cotacoes.json") + ler("card_contato.json") if c["nome"]}
    fotos_json = next((Path(d["arquivo"]).parent / "fotos.json" for d in ocr.values()), None)
    fotos = json.loads(fotos_json.read_text(encoding="utf-8")) if fotos_json and fotos_json.exists() else []
    imagens = {f["rotulo"]: str(fotos_json.parent / f["arquivo"]) for f in fotos}
    imagens.update({rot: d.get("endireitada") or d["arquivo"] for rot, d in ocr.items()})  # documento de lado vai em pé
    campos, faltas = [], []

    def foto_de(origem):  # "Documento do veículo - Aberto (dígito verificador OK)" → rótulo da foto
        # o rótulo mais longo que bate: "Anexo (Atividades): Cnh correta" não pode virar "Anexo (Atividades): Cnh"
        return max((rot for rot in imagens if origem and origem.startswith(rot)), key=len, default="")

    def novo(aba, nome, rotulo, encontrado, fonte, status, nota="", pode=True, atual=None):
        atual = atuais.get(nome, "") if atual is None else atual
        atual = "" if atual in VAZIOS else atual
        campos.append({"aba": aba, "nome": nome, "rotulo": rotulo, "atual": atual, "encontrado": encontrado, "fonte": fonte,
                       "foto": foto_de(fonte), "status": status, "nota": nota,
                       "pode_preencher": pode and status == "sugerido" and nome != "vhclFipeCode"})

    # 1) campos vazios que o CRLV do próprio veículo preenche (as mesmas regras do --preencher)
    try:
        propostas, f = propor_preenchimento(card, pasta_card, ocr)
        faltas += f
        for aba, nome, rotulo, _, valor, origem in propostas:
            novo(aba, nome, rotulo, valor, origem, "sugerido")
    except Exception as e:
        faltas.append(f"CRLV: {str(e)[:120]}")
    if not atuais.get("clntPhoneMobile2"):  # regra interna: sem segundo celular, preencher com zeros
        novo("Contato", "clntPhoneMobile2", "Telefone celular 2", "00000000000", "regra: sem 2º celular, tudo 0", "sugerido")
    feitos = {c["nome"] for c in campos}

    # 1b) documentos pela IA (CNH, selfie, CRLV, comprovante) × card: Contato inteiro e dados do veículo
    from .ocr_teste import cpf_valido

    try:
        bruto = documentos_ia(pasta_card, imagens)
    except Exception as e:
        bruto = None
        faltas.append(f"IA nos documentos: {str(e)[:120]}")
    docs = escolher_documentos(bruto, card, atuais)  # sem documento nenhum: tudo vira "não encontrado em nenhum documento"
    avisos = list(docs.get("avisos") or [])
    fotos_ia = docs.get("_fotos", {})
    fonte = lambda doc: f"{fotos_ia[doc]} (IA {gpt.MODELO})" if doc in fotos_ia else f"nenhum documento ({doc} não enviado)"
    sem = lambda doc, o_que="": "" if doc in fotos_ia else f"não encontrado em nenhum documento: {doc} não foi enviado"
    so_digitos = lambda t: re.sub(r"\D", "", str(t or ""))

    def avaliar(aba, nome, rotulo, achado, origem, atual=None, igual=None, nota_dif="", sem_doc="não encontrado no documento",
                dif="diverge", pode=True):
        """Card × documento: vazio no card → sugestão; igual → confere; diferente → diverge (ou conferir)."""
        if nome in feitos:
            return
        atual = atuais.get(nome, "") if atual is None else atual
        atual = "" if atual in VAZIOS else atual
        if not achado:
            novo(aba, nome, rotulo, "não encontrado", origem, "conferir", sem_doc, atual=atual)
        elif not atual:
            novo(aba, nome, rotulo, achado, origem, "sugerido", pode=pode, atual=atual)
        else:
            bate = (igual or (lambda a, b: _so_letras(a) == _so_letras(b)))(atual, achado)
            novo(aba, nome, rotulo, achado, origem, "ok" if bate else dif, "" if bate else (nota_dif or f"no card: {atual}"),
                 pode=pode, atual=atual)
        feitos.add(nome)

    if docs:
        cnh, crlv_ia, comp = docs.get("cnh") or {}, docs.get("crlv") or {}, docs.get("comprovante") or {}
        f_cnh, sem_cnh = fonte("CNH do associado"), sem("CNH do associado") or "não encontrado na CNH"
        # leitura por IA: diferença vira "conferir" (pode ser erro de leitura); CNH de outra pessoa: nada é sugerido
        dif_cnh, nota_cnh = "conferir", " (leitura da IA: confira no documento)"
        if docs.get("cnh_de_outra_pessoa"):
            avisos.append(f"a CNH enviada parece ser de outra pessoa ({docs['cnh_de_outra_pessoa']}): campos da CNH não sugeridos")
            novo("Contato", "clntFullName", "Nome Completo", docs["cnh_de_outra_pessoa"], f_cnh, "diverge",
                 "a CNH enviada tem outro nome: confira se é do associado", pode=False)
            cnh = {}
            feitos.add("clntFullName")
            sem_cnh = "a CNH enviada é de outra pessoa"
        elif docs.get("cnh_pelo_nome_parcial"):
            parcial = docs["cnh_pelo_nome_parcial"]
            avisos.append(f"no card o nome está incompleto ('{parcial}'): usei a CNH de {cnh.get('nome_completo')} porque o nome bate "
                          "e não há CPF no card para confirmar. Confira se a CNH é mesmo do associado")
            incertezas.anotar(placa, "CNH do associado", cnh.get("nome_completo"),
                              f"CNH aceita só pelo nome incompleto do card ('{parcial}'), sem CPF para confirmar", f_cnh)
        nota_nome = (f"no card só '{docs['cnh_pelo_nome_parcial']}' (nome incompleto): o nome completo na CNH é o encontrado"
                     if docs.get("cnh_pelo_nome_parcial") else "o nome no card é diferente do nome na CNH")
        avaliar("Contato", "clntFullName", "Nome Completo", " ".join(_palavras(cnh.get("nome_completo"))), f_cnh, sem_doc=sem_cnh,
                igual=lambda a, b: _palavras(a) == _palavras(b), nota_dif=nota_nome + nota_cnh, dif=dif_cnh)
        cpf = so_digitos(cnh.get("cpf"))
        if "clntRegistration" not in feitos and cpf and not cpf_valido(cpf):
            novo("Contato", "clntRegistration", "CPF", cpf, f_cnh, "conferir", "dígito verificador não confere: a IA pode ter lido errado")
            feitos.add("clntRegistration")
        avaliar("Contato", "clntRegistration", "CPF", f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}" if len(cpf) == 11 else cpf, f_cnh,
                sem_doc=sem_cnh,
                igual=lambda a, b: so_digitos(a) == so_digitos(b), nota_dif="o CPF do card é diferente do CPF da CNH" + nota_cnh, dif=dif_cnh)
        digitos = lambda a, b: so_digitos(a) == so_digitos(b)
        contem = lambda a, b: _so_letras(a).startswith(_so_letras(b)) or _so_letras(b).startswith(_so_letras(a))
        for nome, rotulo, achado, igual in (
                ("clntRG", "RG", cpf or so_digitos(cnh.get("rg")), digitos),  # RG não é mais usado: padrão é o CPF
                ("clntExpdt", "Órgão Expedidor", (cnh.get("orgao_expedidor") or "").upper(), contem),
                ("clntExpdtDate", "Data Expedição", cnh.get("data_emissao"), digitos),
                ("clntCNH", "CNH (nº registro)", so_digitos(cnh.get("registro")), digitos),
                ("clntfrstQual", "Data 1ª habilitação", cnh.get("primeira_habilitacao"), digitos),
                ("clntCNHExpir", "Validade CNH", cnh.get("validade"), digitos),
                ("clntBirthdate", "Data Nascimento", cnh.get("data_nascimento"), digitos)):
            avaliar("Contato", nome, rotulo, achado, f_cnh, igual=igual, dif=dif_cnh, sem_doc=sem_cnh,
                    nota_dif=f"no card: {atuais.get(nome, '')} · na CNH: {achado}" + nota_cnh)
        # Categoria: seleção múltipla escondida (fSelect); o valor atual a extensão lê na tela e compara
        avaliar("Contato", "clntCNHCategory", "Categoria", re.sub(r"[^A-E]", "", (cnh.get("categoria") or "").upper()), f_cnh,
                atual="", sem_doc=sem_cnh)
        sexo = {"masculino": "Masculino", "feminino": "Feminino"}.get(docs.get("sexo"), "")
        avaliar("Contato", "clntGender", "Sexo", sexo, fonte("selfie do associado"), dif="conferir",
                sem_doc="não encontrado: nenhuma foto mostra o associado", nota_dif="o card diz outro; confira pela foto")

        # telefone principal e número do endereço: confirmados nos documentos ou "não encontrado em nenhum documento"
        textos = " ".join(" ".join(d.get("texto") or []) for d in ocr.values())
        fones = list(docs.get("telefones") or [])
        fones += [(m, "OCR dos documentos") for m in re.findall(r"\d{10,11}", re.sub(r"[\s().-]", "", textos))]
        celular = so_digitos(atuais.get("clntPhoneMobile1"))
        if "clntPhoneMobile1" not in feitos:
            achado = next(((n, doc) for n, doc in fones if celular and len(n) >= 8 and (n.endswith(celular[-8:]))), None)
            if celular and achado:
                novo("Contato", "clntPhoneMobile1", "Telefone celular", atuais["clntPhoneMobile1"], f"{achado[1]} (IA/OCR)", "ok")
            elif celular:
                novo("Contato", "clntPhoneMobile1", "Telefone celular", "não encontrado em nenhum documento", "CNH, CRLV e comprovante",
                     "conferir", "o número do card não aparece em nenhum documento enviado")
            elif fones:
                novo("Contato", "clntPhoneMobile1", "Telefone celular", fones[0][0], f"{fones[0][1]} (IA/OCR)", "sugerido")
            feitos.add("clntPhoneMobile1")
        f_comp = fonte("comprovante de endereço")
        sem_comp = sem("comprovante de endereço") or "não encontrado no comprovante"
        avaliar("Contato", "clntAddressZipcode", "CEP", so_digitos(comp.get("cep")), f_comp, sem_doc=sem_comp, igual=digitos)
        avaliar("Contato", "clntAddress", "Endereço", (comp.get("logradouro") or "").upper(), f_comp, sem_doc=sem_comp, dif="conferir",
                igual=lambda a, b: len(set(_palavras(a)) & set(_palavras(b))) >= 2)
        avaliar("Contato", "clntAddressNumber", "Número", so_digitos(comp.get("numero")), f_comp,
                sem_doc=sem("comprovante de endereço") or "não encontrado em nenhum documento", igual=digitos, dif="conferir")
        avaliar("Contato", "clntAddressNeighborhood", "Bairro", (comp.get("bairro") or "").upper(), f_comp, sem_doc=sem_comp, dif="conferir",
                igual=lambda a, b: _palavras(a) == _palavras(b))

        # veículo (aba Cotações): CRLV + FIPE oficial + fotos
        f_crlv = fonte("CRLV do veículo")
        marca_card, _, resto = card["veiculo"].partition(",")
        ano_card = (re.search(r"(19|20)\d{2}$", card["veiculo"]) or [""])[0]
        modelo_card = resto.strip()[: -len(ano_card)].strip() if ano_card else resto.strip()
        vist = (r.get("vistoria_ia") or {}).get("veiculo") or {}
        especie = regras.sem_acento(f"{crlv_ia.get('especie_tipo', '')} {vist.get('tipo', '')}").upper()
        tipo = next((op for padrao, op in TIPO_CRM if re.search(padrao, especie)), "")
        avaliar("Cotações", "vhclType", "Tipo de veículo", tipo, f_crlv if crlv_ia.get("especie_tipo") else "fotos da vistoria (IA)",
                atual=card.get("tipo_veiculo", ""), igual=lambda a, b: _palavras(a)[:1] == _palavras(b)[:1])
        oficial = (r.get("fipe") or {}).get("oficial") or {}
        marca_doc = crlv_ia.get("marca") or oficial.get("Marca", "")
        avaliar("Cotações", "vhclBrand", "Marca", marca_doc.upper(), f_crlv if crlv_ia.get("marca") else "FIPE oficial",
                atual=marca_card.strip(), pode=False,
                igual=lambda a, b: _so_letras(a) in _so_letras(b) or _so_letras(b) in _so_letras(a))
        ano_chassi = (r.get("fipe") or {}).get("ano_pelo_chassi")
        ano_doc = so_digitos(crlv_ia.get("ano_modelo"))[-4:]
        avaliar("Cotações", "vhclFbrctnYrMdl", "Ano modelo", ano_doc or (str(ano_chassi) if ano_chassi else ""),
                f_crlv + (f" · chassi indica {ano_chassi}" if ano_chassi else ""), atual=ano_card, pode=False)
        if "vhclModel" not in feitos:
            fp = r.get("fipe") or {}
            versao = (fp.get("ia") or {}).get("versao") or ""
            st = {"OK": "ok", "DIVERGENTE": "diverge"}.get(fp.get("status"), "conferir")
            novo("Cotações", "vhclModel", "Modelo", " · ".join(filter(None, [crlv_ia.get("modelo") and f"CRLV: {crlv_ia['modelo']}",
                                                                              versao and f"IA/FIPE: {versao}"])) or "não encontrado",
                 f"{f_crlv} + FIPE oficial {fp.get('mes', '')}", st, "" if st == "ok" else (fp.get("detalhe") or "")[:400],
                 pode=False, atual=modelo_card)
            feitos.add("vhclModel")
        cor_doc = (crlv_ia.get("cor") or vist.get("cor") or "").upper()
        cor = next((c for c in CORES_CRM if regras.sem_acento(cor_doc)[:4] == regras.sem_acento(c).upper()[:4]), cor_doc.title())
        nota_cor = "" if not (vist.get("cor") and crlv_ia.get("cor")) or regras.sem_acento(vist["cor"]).upper()[:4] == \
            regras.sem_acento(crlv_ia["cor"]).upper()[:4] else f"nas fotos a IA viu {vist['cor']}"
        avaliar("Cotações", "vhclClr", "Cor", cor, f_crlv if crlv_ia.get("cor") else "fotos da vistoria (IA)", atual="")
        if nota_cor and campos and campos[-1]["nome"] == "vhclClr":
            campos[-1]["nota"] = nota_cor
        uf_nome = {sigla: nome for nome, sigla in regras.UF.items()}
        locais = [(uf_nome.get((crlv_ia.get("uf") or "").upper(), crlv_ia.get("uf") or ""), crlv_ia.get("municipio") or "", "CRLV"),
                  (uf_nome.get((comp.get("uf") or "").upper(), comp.get("uf") or ""), comp.get("cidade") or "", "comprovante")]
        locais = [x for x in locais if x[0] or x[1]]
        for nome, rotulo, atual, i in (("qttnClientState", "Estado de circulação", card.get("estado_circulacao", ""), 0),
                                       ("qttnClientCity", "Cidade de circulação", card.get("cidade_circulacao", ""), 1)):
            if nome in feitos:
                continue
            achados = [(x[i], x[2]) for x in locais if x[i]]
            bate = next((a for a in achados if _palavras(a[0]) == _palavras(atual)), None)
            if not achados:
                novo("Cotações", nome, rotulo, "não encontrado", "CRLV e comprovante", "conferir", "não encontrado em nenhum documento",
                     pode=False, atual=atual)
            else:
                novo("Cotações", nome, rotulo, " · ".join(f"{a} ({doc})" for a, doc in achados), f_crlv,
                     "ok" if bate else "conferir", "" if bate else "circulação diferente do registro/endereço: confira", pode=False, atual=atual)
            feitos.add(nome)
        if sexo:  # os campos de documento ganham a segunda leitura do OCR mais abaixo; o sexo só a IA vê
            incertezas.anotar(placa, "Sexo (foto)", sexo, "uma leitura só (IA na foto): confira na selfie", fonte("selfie do associado"))
        # contas (dígitos, datas) + OCR local lendo a mesma imagem: a certeza vem daí, não da IA
        try:
            checagem = validar_docs.checar(pasta_card, docs, imagens, ocr)
        except Exception as e:
            checagem = {}
            faltas.append(f"conferência OCR × IA: {str(e)[:120]}")
        for c in campos:
            ch = checagem.get(c["nome"])
            if not ch or c["encontrado"] in ("", "não encontrado") or "(IA " not in c["fonte"]:
                continue
            if ch["erro"]:
                c.update(status="conferir", pode_preencher=False, fonte=c["fonte"] + " · conta não fecha",
                         nota=f"{ch['erro']}: a IA leu errado, confira no documento" + (f" · {c['nota']}" if c["nota"] else ""))
                incertezas.anotar(placa, c["rotulo"], c["encontrado"], ch["erro"], c["fonte"])
            elif ch["ocr"]:
                c["fonte"] += " · OCR local confirma"
            elif c["status"] == "ok":  # o card (digitado do documento) bate com a IA: duas leituras independentes
                c["fonte"] += " · card confere"
            else:
                c["fonte"] += " · só a IA leu"
                if c["status"] == "sugerido":
                    c["nota"] = ((c["nota"] + " · ") if c["nota"] else "") + "só a IA leu (o OCR local não confirmou): confira no documento"
                incertezas.anotar(placa, c["rotulo"], c["encontrado"], "uma leitura só (IA; o OCR local não confirmou)", c["fonte"])

    # 2) campos já preenchidos × CRLV do próprio veículo
    crlv = next(((rot, d) for rot, d in ocr.items() if d["crlv"] and "OK" in d["conferencia"].values()), None)
    if crlv:
        for chave, situacao_doc in crlv[1]["conferencia"].items():
            if chave in CRLV_CAMPO and CRLV_CAMPO[chave][0] not in feitos:
                nome, rotulo = CRLV_CAMPO[chave]
                lido = (crlv[1]["achados"].get(chave) or [""])[0]
                novo("Cotações", nome, rotulo, lido, crlv[0], "ok" if situacao_doc == "OK" else "diverge",
                     "" if situacao_doc == "OK" else f"CRLV diz {lido}")
                feitos.add(nome)

    # 3) km do painel (IA nas fotos): só sugestão, sempre conferir
    painel = (r.get("vistoria_ia") or {}).get("painel") or {}
    if not atuais.get("vhclKmtr") and painel.get("km"):
        novo("Cotações", "vhclKmtr", "Km", painel["km"][0], (painel.get("fotos") or [""])[0], "sugerido",
             "lido pela IA na foto do painel: confira olhando a foto")
        feitos.add("vhclKmtr")

    # 4) FIPE oficial do mês da cotação (as provas dela também trazem o câmbio visto no painel)
    fp = r.get("fipe") or {}
    cambio = ((fp.get("provas") or {}).get("cambio") or "").lower()
    if atuais.get("vhclShft") in ("", "Selecione", None) and "vhclShft" not in feitos and cambio.startswith(("manual", "autom")):
        origem = re.search(r"foto '([^']+)'", cambio)
        novo("Cotações", "vhclShft", "Câmbio", "Manual" if cambio.startswith("manual") else "Automático",
             next((rot for rot in imagens if origem and rot.lower() == origem.group(1)), "fotos do painel"), "sugerido",
             "visto pela IA na foto do painel/câmbio: confira olhando a foto")
        feitos.add("vhclShft")
    oficial = fp.get("oficial") or {}
    if oficial.get("Valor") and "vhclFipeVl" not in feitos:
        bate = _so_letras(oficial["Valor"]) == _so_letras(atuais.get("vhclFipeVl"))
        depois = fp.get("valor_atualizado")  # o CRM atualizou o valor num mês seguinte ao da cotação
        if not bate and depois and _so_letras(depois["valor"]) == _so_letras(atuais.get("vhclFipeVl")):
            novo("Cotações", "vhclFipeVl", "Valor FIPE", depois["valor"], f"FIPE oficial {depois['mes']}", "ok",
                 f"valor da tabela de {depois['mes']} (cotação de {fp.get('mes', '')}, que era {oficial['Valor']}): o CRM atualizou")
        else:
            novo("Cotações", "vhclFipeVl", "Valor FIPE", oficial["Valor"], f"FIPE oficial {fp.get('mes', '')}",
                 "ok" if bate else "diverge", "" if bate else f"FIPE oficial: {oficial['Valor']}")
        feitos.add("vhclFipeVl")
    escolhida = ((fp.get("ia") or {}).get("versao") or "")
    if fp and "vhclFipeCode" not in feitos:
        cand = next((c for c in fp.get("candidatas") or [] if c.get("modelo") == escolhida), {})
        outra = escolhida and cand.get("codigo_fipe") and cand["codigo_fipe"] != atuais.get("vhclFipeCode")
        novo("Cotações", "vhclFipeCode", "Código FIPE",
             f"{cand['codigo_fipe']} · {escolhida} · {cand.get('valor', '')}" if outra else oficial.get("CodigoFipe", ""),
             f"FIPE oficial {fp.get('mes', '')} + IA ({(fp.get('ia') or {}).get('certeza', '?')} certeza)",
             "ok" if fp.get("status") == "OK" else "diverge" if fp.get("status") == "DIVERGENTE" else "conferir",
             fp.get("detalhe", "")[:400] + (" · o robô não troca o código FIPE: só sugere" if outra else ""), pode=False)
        feitos.add("vhclFipeCode")

    # 5) o resto da conferência que tem campo na tela
    for chave, (status, detalhe) in r["itens"].items():
        if chave in ITEM_CAMPO and ITEM_CAMPO[chave][0] not in feitos:
            nome, rotulo = ITEM_CAMPO[chave]
            novo("Cotações", nome, rotulo, "", NOMES.get(chave, chave), SITUACAO_CAMPO.get(status, "conferir"), detalhe)

    vistoria = r.get("vistoria_ia") or {}
    fora = {x["foto"]: x["e"] for x in vistoria.get("fora_do_lugar") or []}
    avisos += [f"foto no lugar errado: o espaço '{rot}' recebeu {e}" for rot, e in fora.items()]
    # central de fotos da extensão: cada imagem com a origem, o que a IA viu nela e o documento que ela virou
    vistas = _ler_json(pasta_card / "analise_fotos.json").get("fotos") or {}
    usada = {rot: doc for doc, rot in fotos_ia.items()}
    lida = {i["rotulo"]: i.get("tipo") for i in (bruto or {}).get("imagens") or [] if i.get("rotulo")}
    galeria = [{"rotulo": f["rotulo"], "origem": _origem(f), "data": f.get("data") or "", "analise": vistas.get(f["rotulo"]),
                "lida_como": lida.get(f["rotulo"], ""), "usada_como": usada.get(f["rotulo"], ""), "fora_do_lugar": fora.get(f["rotulo"], "")}
               for f in fotos if f["rotulo"] in imagens]
    return {
        "codigo": codigo, "placa": placa, "veiculo": card["veiculo"], "situacao": r["situacao"],
        "analisado_em": f"{datetime.now():%d/%m/%Y %H:%M}", "segundos": segundos, "custo_rs": custo,
        "campos": campos, "faltas": faltas, "avisos": avisos,
        "conferencia": [{"nome": NOMES.get(k, k), "status": s, "detalhe": d, "no_campo": k in ITEM_CAMPO} for k, (s, d) in r["itens"].items()],
        "fipe": {"status": fp.get("status"), "detalhe": fp.get("detalhe"), "mes": fp.get("mes"), "valor": oficial.get("Valor"),
                 "modelo": oficial.get("Modelo"), "codigo": oficial.get("CodigoFipe"), "autenticacao": oficial.get("Autenticacao")},
        "vistoria": {"estado_geral": max((k for k in vistoria.get("estado_geral") or {} if k != "nao_da_para_avaliar"),
                                     key=vistoria["estado_geral"].get, default=""), "km": painel.get("km"), "luzes": painel.get("luzes"),
                     "vidros": vistoria.get("vidros_danos", []), "fotos_com_problema": vistoria.get("fotos_com_problema", []),
                     "avarias": [a for a in vistoria.get("avarias", []) if a.get("gravidade") != "leve"],
                     "leves": sum(a.get("gravidade") == "leve" for a in vistoria.get("avarias", [])),
                     "fora_do_lugar": vistoria.get("fora_do_lugar", [])} if vistoria else None,
        "fotos": galeria,
        "incertezas": [x for x in incertezas.ANOTADAS if x["card"] == placa],
        "_imagens": imagens,
    }


def _codigo_do_card(pasta_card):
    texto = (pasta_card / "card_atividades.txt").read_text(encoding="utf-8") if (pasta_card / "card_atividades.txt").exists() else ""
    m = re.search(r"ID:\s*(\w+)", texto)
    return m.group(1) if m else ""


def guardar(pasta_exec, res, pedido):
    """Salva resultado.json e registra o trabalho (a extensão acha pelo código do card ou pela placa)."""
    (pasta_exec / "resultado.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    incertezas.salvar(pasta_exec)
    incertezas.ANOTADAS.clear()
    dados = {"estado": "pronto", "placa": res["placa"], "veiculo": res["veiculo"], "situacao": res["situacao"],
             "pasta": str(pasta_exec), "fim": time.time(), "campos": len(res["campos"]),
             "sugeridos": sum(c["status"] == "sugerido" for c in res["campos"]), "custo_rs": res["custo_rs"]}
    _atualizar(pedido, codigo=res["codigo"], **dados)


def carregar_indice():
    if INDICE.exists() and not TRABALHOS:
        TRABALHOS.update(json.loads(INDICE.read_text(encoding="utf-8")))


def importar(pasta_demo):
    """Monta o resultado de uma demo já rodada (sem abrir o CRM; a IA só lê os documentos se ainda não leu)."""
    carregar_indice()
    pasta_demo = Path(pasta_demo)
    r = json.loads((pasta_demo / "relatorio.json").read_text(encoding="utf-8"))[0]
    pasta_card = next(p for p in pasta_demo.iterdir() if p.is_dir() and (p / "card_cotacoes.json").exists())
    codigo = _codigo_do_card(pasta_card) or r["card"]["placa"]
    res = montar(pasta_card, r, codigo)
    guardar(pasta_demo, res, codigo)
    print(f"importado {codigo} · {res['placa']} · {len(res['campos'])} campo(s), "
          f"{sum(c['status'] == 'sugerido' for c in res['campos'])} sugerido(s)")
    return res


# ---------------- análise (uma de cada vez, navegador próprio, só leitura) ----------------

def _acrescentar(pedido, chave, valor):
    """Lista ao vivo do trabalho (chega de várias threads: fotos analisadas em paralelo)."""
    with _trava:
        TRABALHOS.setdefault(pedido, {})
        TRABALHOS[pedido][chave] = TRABALHOS[pedido].get(chave, []) + [valor]


def _acompanhar(pedido):
    """Durante a conferência o lote avisa o painel (etapa, documento lido, foto vista). Aqui esses avisos vão para o
    trabalho, e a página do assistente anima o card. Devolve uma função que desfaz a troca."""
    from . import painel

    antes = (painel.etapa, painel.passo, painel.documento, painel.vistoria_foto)
    painel.etapa = lambda n, titulo: _atualizar(pedido, etapa=titulo[:90], detalhe="")
    # texto None = a etapa terminou (a FIPE manda passo(None, "confirmado")): mostra o resultado
    painel.passo = lambda grupo, texto, feito="": _atualizar(pedido, detalhe=(texto or feito or "")[:90])
    painel.documento = lambda rotulo, dados, arquivo: _acrescentar(pedido, "lidos", rotulo)
    painel.vistoria_foto = lambda rotulo, resposta: _acrescentar(pedido, "vistas", rotulo)

    def desfazer():
        painel.etapa, painel.passo, painel.documento, painel.vistoria_foto = antes
    return desfazer


def miniatura(pedido, i):
    """Foto reduzida (260 px) do card em análise, para a animação da página."""
    import cv2

    from .ocr_teste import ler_imagem

    t = TRABALHOS.get(pedido) or {}
    arquivos = t.get("arquivos") or []
    if not t.get("pasta_fotos") or not 0 <= i < len(arquivos):
        return None
    chave = (t["pasta_fotos"], i)
    if chave not in _MINIATURAS:
        try:
            img = ler_imagem(Path(t["pasta_fotos"]) / arquivos[i])
        except Exception:
            return None
        if img is None:
            return None
        recente = time.time() - (Path(t["pasta_fotos"]) / arquivos[i]).stat().st_mtime < 1.5
        escala = 260 / max(img.shape[1], 1)
        img = cv2.resize(img, (260, max(1, int(img.shape[0] * escala))), interpolation=cv2.INTER_AREA)
        foto = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 72])[1].tobytes()
        if recente:
            return foto
        _guardar_miniatura(chave, foto)
        return foto
    return _MINIATURAS[chave]


_MINIATURAS = {}
LIMITE_MINIATURAS = 600  # ~15 MB; passou disso, começa de novo (as fotos são refeitas quando alguém pedir)


def _guardar_miniatura(chave, foto):
    if len(_MINIATURAS) >= LIMITE_MINIATURAS:
        _MINIATURAS.clear()
    _MINIATURAS[chave] = foto


def reduzida(caminho, largura=220):
    """Foto do resultado reduzida (miniatura da central de fotos). Guarda na memória: a faixa pede todas de uma vez."""
    import cv2

    from .ocr_teste import ler_imagem

    if caminho not in _MINIATURAS:
        img = ler_imagem(caminho)
        if img is None:
            return None
        escala = largura / max(img.shape[1], 1)
        img = cv2.resize(img, (largura, max(1, int(img.shape[0] * escala))), interpolation=cv2.INTER_AREA)
        foto = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tobytes()
        _guardar_miniatura(caminho, foto)
        return foto
    return _MINIATURAS[caminho]


TITULOS_TELA = {"01_crm": "PowerCRM aberto", "02_busca": "Buscando o card no funil", "card_contato": "Aba Contato",
                "card_cotacoes": "Aba Cotações", "card_vistoria": "Aba Vistoria"}
FOTO_BAIXADA = re.compile(r"^(\d{2})_(.+)\.(jpe?g|png|webp)$", re.I)


def _foto_tela(page, pasta_exec, nome):
    """Foto temporária da tela do CRM para a página mostrar o robô andando (se travar, segue sem ela)."""
    try:
        (pasta_exec / "telas").mkdir(exist_ok=True)
        page.screenshot(path=str(pasta_exec / "telas" / f"{nome}.png"), timeout=8000, animations="disabled")
    except Exception:
        pass


def _vigiar(pedido, inicio, parar):
    """Enquanto o robô está no CRM: as telas que ele salva e as fotos que vão sendo baixadas entram no trabalho, uma a uma."""
    while not parar.wait(0.7):
        with _trava:
            t = dict(TRABALHOS.get(pedido) or {})
        if t.get("estado") != "rodando":
            break
        pasta = Path(t.get("pasta") or ".")
        telas = sorted((f for f in list(pasta.glob("telas/*.png")) + list(pasta.glob("*/card_*.png")) if f.stem in TITULOS_TELA),
                       key=lambda f: f.stat().st_mtime)
        if len(telas) != len(t.get("telas") or []):
            _atualizar(pedido, telas=[{"titulo": TITULOS_TELA[f.stem], "arquivo": str(f)} for f in telas])
        if not t.get("placa"):
            continue
        amostras = config.PASTA_AMOSTRAS / t["placa"]
        try:
            novas = sorted(f.name for f in amostras.iterdir() if FOTO_BAIXADA.match(f.name) and f.stat().st_mtime >= inicio - 1)
        except OSError:
            continue
        if novas != (t.get("arquivos") or []):
            rotulo = lambda n: FOTO_BAIXADA.match(n).group(2).replace("___", " - ").replace("_", " ").capitalize()
            _atualizar(pedido, pasta_fotos=str(amostras), arquivos=novas, fotos=[rotulo(n) for n in novas])


def tela(pedido, i):
    """Tela do CRM reduzida (só o topo), para a janelinha da página."""
    import cv2

    from .ocr_teste import ler_imagem

    telas = (TRABALHOS.get(pedido) or {}).get("telas") or []
    if not 0 <= i < len(telas):
        return None
    img = ler_imagem(telas[i]["arquivo"])
    img = img[: int(img.shape[1] * 0.62)]  # recorte 16:10 do topo (as abas do card são páginas compridas)
    escala = 720 / max(img.shape[1], 1)
    img = cv2.resize(img, (720, max(1, int(img.shape[0] * escala))), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()


def analisar(pedido):
    from playwright.sync_api import sync_playwright

    from .explorar import buscar_card, gravar_rede, ler_card, salvar
    from .lote import conferir_card
    from .sessao import SessaoExpirada, abrir, esquecer_sessao, proteger

    incertezas.ANOTADAS.clear()  # o card anterior pode ter parado no meio, antes de guardar(): começa limpo
    inicio, chamadas = time.time(), len(custo_ia.DESTA_EXECUCAO)
    limpo = re.sub(r"\W", "", pedido)
    pasta_exec = PASTA / f"{datetime.now():%Y%m%d-%H%M%S}-{limpo}"
    pasta_exec.mkdir(parents=True, exist_ok=True)
    _atualizar(pedido, estado="rodando", etapa="abrindo o PowerCRM (só leitura)", inicio=inicio, pasta=str(pasta_exec),
               fotos=[], arquivos=[], lidos=[], vistas=[], telas=[], detalhe="", fase="crm", placa="", veiculo="")
    parar = threading.Event()
    vigia = threading.Thread(target=_vigiar, args=(pedido, inicio, parar), daemon=True)
    vigia.start()
    with sync_playwright() as p:
        browser, ctx, page = abrir(p, login_automatico=True)
        try:
            proteger(ctx)
            rede = gravar_rede(ctx)
            _foto_tela(page, pasta_exec, "01_crm")
            _atualizar(pedido, etapa="buscando o card no funil")
            try:
                achados = buscar_card(page, pedido)
            except SessaoExpirada:  # a sessão salva venceu: apaga e abre a janela para o login (uma vez)
                browser.close()
                esquecer_sessao()
                _atualizar(pedido, etapa="sessão do PowerCRM vencida: entrando de novo com o login do .env")
                browser, ctx, page = abrir(p, login_automatico=True)
                proteger(ctx)
                rede = gravar_rede(ctx)
                _atualizar(pedido, etapa="buscando o card no funil")
                achados = buscar_card(page, pedido)
            _foto_tela(page, pasta_exec, "02_busca")
            if not achados:
                return _atualizar(pedido, estado="erro", etapa="", erro="nenhum card encontrado com essa busca")
            livres = [c for c in achados if not c["analistas"]]
            if not livres:
                return _atualizar(pedido, estado="pulado", etapa="", erro=f"card com analista ({', '.join(achados[0]['analistas'])}): não foi aberto")
            alvo = livres[0]
            pasta_card = pasta_exec / alvo["placa"]
            pasta_card.mkdir(parents=True, exist_ok=True)
            salvar(pasta_card, "cards_filtro.json", [alvo])
            _atualizar(pedido, placa=alvo["placa"], veiculo=alvo["veiculo"], etapa="lendo o card e baixando as fotos")
            pasta_fotos, _ = ler_card(page, ctx, alvo, pasta_card, rede, busca=pedido)
        finally:
            browser.close()
            parar.set()
            vigia.join(3)
    fotos = json.loads((pasta_fotos / "fotos.json").read_text(encoding="utf-8"))
    _atualizar(pedido, etapa="lendo os arquivos (OCR local)", fase="analise", pasta_fotos=str(pasta_fotos),
               fotos=[f["rotulo"] for f in fotos], arquivos=[f["arquivo"] for f in fotos])
    desfazer = _acompanhar(pedido)
    try:
        r = conferir_card(pasta_card, pasta_fotos, decidir_tabela.montar_regras())
    finally:
        desfazer()
    salvar(pasta_card, "conferencia.json", r)
    _atualizar(pedido, etapa="IA lendo os documentos (CNH, CRLV, comprovante) e montando o resultado", fase="documentos")
    custo = sum(d or 0 for _, _, d in custo_ia.DESTA_EXECUCAO[chamadas:]) * custo_ia.DOLAR
    res = montar(pasta_card, r, _codigo_do_card(pasta_card) or pedido, round(time.time() - inicio), round(custo, 4))
    guardar(pasta_exec, res, pedido)


# ---------------- análise automática ----------------
# Quando ninguém pediu nada, o robô lê a lista do filtro das analistas (o mesmo do lote: só lê o funil, não põe tag),
# analisa um card novo por vez e, ao acabar a lista, lê de novo. Pedido de analista passa sempre na frente.

ARQ_AUTO = PASTA / "auto.json"  # ligado/desligado (a tela /admin muda)
ESPERA_SEM_NOVIDADE = 120  # s até ler a lista de novo quando não apareceu card novo
ESPERA_ERRO_LISTA = 300  # s até tentar de novo quando não deu para ler a lista
TENTATIVAS_AUTO = 2  # card que deu erro é tentado de novo só até aqui (não fica em loop)
AUTO = {"pendentes": [], "proxima": 0.0, "ultima_lista": None, "na_lista": 0, "novos": 0, "erro": "",
        "tentativas": {}, "lendo": False}


def auto_ligado():
    try:
        return bool(json.loads(ARQ_AUTO.read_text(encoding="utf-8")).get("ligado", True))
    except (OSError, ValueError):
        return config.ENV.get("auto", "1") != "0"


def ligar_auto(ligado):
    ARQ_AUTO.parent.mkdir(parents=True, exist_ok=True)
    ARQ_AUTO.write_text(json.dumps({"ligado": bool(ligado)}), encoding="utf-8")
    if ligado:
        AUTO["proxima"] = 0.0  # lê a lista já
    print(f"{datetime.now():%H:%M:%S} · análise automática {'ligada' if ligado else 'desligada'}")


def _ja_resolvido(codigo):
    """Já analisado (ou pulado, ou na fila/rodando) por código, qualquer que tenha sido a forma de pedir."""
    with _trava:
        for k, v in TRABALHOS.items():
            if codigo in (k, v.get("codigo")) and v.get("estado") in ("pronto", "pulado", "fila", "rodando"):
                return True
    return AUTO["tentativas"].get(codigo, 0) >= TENTATIVAS_AUTO


def _ler_lista_do_filtro():
    from playwright.sync_api import sync_playwright

    from .lote import listar, resumo
    from .sessao import abrir, esquecer_sessao, proteger

    with sync_playwright() as p:
        browser, ctx, page = abrir(p, login_automatico=True)
        try:
            proteger(ctx)
            itens, total, _ = listar(page, None, None)
        finally:
            browser.close()
    if total is None:  # não veio a contagem do funil: sessão vencida ou CRM fora; a próxima tentativa entra de novo
        esquecer_sessao()
        raise RuntimeError("não deu para ler a lista do filtro (sessão do CRM vencida ou CRM fora do ar)")
    return [resumo(i) for i in itens]


def _proximo_automatico():
    """Código do próximo card para a análise automática, ou None (desligada, esperando ou nada novo)."""
    if not auto_ligado():
        return None
    while AUTO["pendentes"]:
        codigo = AUTO["pendentes"].pop(0)
        if not _ja_resolvido(codigo):
            AUTO["tentativas"][codigo] = AUTO["tentativas"].get(codigo, 0) + 1
            _atualizar(codigo, estado="fila", etapa="", erro="", inicio=time.time(), por=["automático"])
            return codigo
    if time.time() < AUTO["proxima"]:
        return None
    AUTO["lendo"] = True
    try:
        cards = _ler_lista_do_filtro()
    except BaseException as e:  # SystemExit do login também: o servidor continua
        AUTO.update(erro=str(e)[:200] or type(e).__name__, proxima=time.time() + ESPERA_ERRO_LISTA)
        print(f"{datetime.now():%H:%M:%S} · análise automática: {AUTO['erro']}")
        return None
    finally:
        AUTO["lendo"] = False
    livres = [c["codigo"] for c in cards if c["placa"] and not c["analistas"]]
    novos = [c for c in livres if not _ja_resolvido(c)]
    AUTO.update(pendentes=novos, ultima_lista=time.time(), na_lista=len(cards), novos=len(novos), erro="",
                proxima=0.0 if novos else time.time() + ESPERA_SEM_NOVIDADE)
    print(f"{datetime.now():%H:%M:%S} · análise automática: {len(cards)} card(s) no filtro, {len(livres)} sem analista, "
          f"{len(novos)} novo(s) para analisar")
    return _proximo_automatico() if novos and FILA.empty() else None  # analista pediu enquanto lia? ele vai primeiro


def situacao_auto():
    return {"ligado": auto_ligado(), "lendo_lista": AUTO["lendo"], "pendentes": len(AUTO["pendentes"]),
            "na_lista": AUTO["na_lista"], "novos_na_ultima": AUTO["novos"], "erro": AUTO["erro"],
            "ultima_lista": AUTO["ultima_lista"], "proxima": AUTO["proxima"] or None}


def _trabalhador():
    while True:
        try:
            pedido = FILA.get_nowait()  # pedido de analista sempre primeiro
        except queue.Empty:
            pedido = _proximo_automatico()
            if not pedido:
                try:
                    pedido = FILA.get(timeout=3)
                except queue.Empty:
                    continue
        try:
            analisar(pedido)
        except BaseException as e:  # SystemExit do login, erro de rede...: o servidor continua de pé
            _atualizar(pedido, estado="erro", etapa="", erro=str(e)[:200] or type(e).__name__)
            traceback.print_exc()
        finally:
            incertezas.ANOTADAS.clear()
            del custo_ia.DESTA_EXECUCAO[:]  # o custo de cada card já foi para o resultado e para custos_ia.csv
        if TRABALHOS.get(pedido, {}).get("estado") == "rodando":
            _atualizar(pedido, estado="erro", etapa="", erro="parou sem resultado")


def _trabalho(chave):
    """O último trabalho pronto do card, pelo código ou pela placa (com ou sem hífen)."""
    with _trava:
        prontos = [dict(x) for x in TRABALHOS.values() if x.get("estado") == "pronto"]
    t = next((x for x in sorted(prontos, key=lambda x: -x.get("fim", 0))
              if chave in (x.get("codigo"), x.get("placa")) or _so_letras(x.get("placa")) == _so_letras(chave)), None)
    return t


def resultado(chave):
    t = _trabalho(chave)
    if not t:
        return None
    res = json.loads((Path(t["pasta"]) / "resultado.json").read_text(encoding="utf-8"))
    obs = Path(t["pasta"]) / "observacoes.json"
    res["observacoes"] = json.loads(obs.read_text(encoding="utf-8")) if obs.exists() else []
    return res


def analistas():
    """chave → nome. Lido a cada pedido: chave nova vale sem reiniciar o servidor."""
    try:
        return json.loads(ANALISTAS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def nova_chave(nome):
    import secrets

    chave = secrets.token_urlsafe(12)
    with _trava:
        todas = analistas()
        todas[chave] = nome.strip()[:60]
        ANALISTAS.parent.mkdir(parents=True, exist_ok=True)
        ANALISTAS.write_text(json.dumps(todas, ensure_ascii=False, indent=1), encoding="utf-8")
    return chave


def ips_da_rede():
    import socket

    try:
        return sorted({i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)} - {"127.0.0.1"})
    except OSError:
        return []


def posicao(pedido):
    """Lugar na fila (1 = o próximo) e o que está rodando agora, para todo mundo ver a mesma fila."""
    with _trava:
        fila = sorted((k for k, v in TRABALHOS.items() if v.get("estado") == "fila"), key=lambda k: TRABALHOS[k].get("inicio", 0))
        rodando = next(({"pedido": k, "placa": v.get("placa"), "por": v.get("por") or []}
                        for k, v in TRABALHOS.items() if v.get("estado") == "rodando"), None)
    return {"posicao": fila.index(pedido) + 1 if pedido in fila else None, "na_fila": len(fila), "rodando_agora": rodando}


BANCO = PASTA / "assistente.db"  # SQLite: as observações dos analistas (a tela /admin lê daqui)


def _banco():
    """Conexão nova a cada uso (o servidor atende em várias threads). Cria a tabela e, na primeira vez,
    traz o que já estava no observacoes.csv."""
    import sqlite3
    from contextlib import closing, contextmanager

    @contextmanager
    def abrir():
        BANCO.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(BANCO, timeout=10)) as db:
            with db:  # commit no fim (rollback se der erro)
                yield db

    return abrir()


def preparar_banco():
    with _banco() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS observacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, data_hora TEXT, codigo TEXT, placa TEXT, analista TEXT, texto TEXT,
            campos_errados TEXT, preenchidos TEXT, pasta TEXT)""")
        if db.execute("SELECT COUNT(*) FROM observacoes").fetchone()[0] or not OBSERVACOES.exists():
            return
        with OBSERVACOES.open(encoding="utf-8-sig", newline="") as f:
            linhas = list(csv.DictReader(f, delimiter=";"))
        db.executemany("INSERT INTO observacoes (data_hora, codigo, placa, analista, texto, campos_errados, preenchidos, pasta) "
                       "VALUES (?,?,?,?,?,?,?,?)",
                       [(l.get("data_hora"), l.get("codigo"), l.get("placa"), l.get("analista"), l.get("observacao"),
                         json.dumps(l.get("campos_errados") or "", ensure_ascii=False),
                         json.dumps(l.get("preenchidos_pela_ia") or "", ensure_ascii=False), l.get("pasta")) for l in linhas])
        print(f"   banco: {len(linhas)} observação(ões) antigas trazidas do observacoes.csv")


def _gravar_no_banco(obs, pasta):
    with _banco() as db:
        db.execute("INSERT INTO observacoes (data_hora, codigo, placa, analista, texto, campos_errados, preenchidos, pasta) "
                   "VALUES (?,?,?,?,?,?,?,?)",
                   (obs["data_hora"], obs["codigo"], obs["placa"], obs["analista"], obs["texto"],
                    json.dumps(obs["campos_errados"], ensure_ascii=False), json.dumps(obs["preenchidos"], ensure_ascii=False), str(pasta)))


def observar(dados, quem=""):
    """Observação do analista: o que estava errado no card ou o que a IA sugeriu/preencheu errado.
    quem: nome da chave (pela rede o nome vem da chave, não do que foi digitado)."""
    t = _trabalho(str(dados.get("codigo", "")))
    if not t:
        raise ValueError("card não analisado")
    corta = lambda v, n: str(v or "")[:n]
    obs = {"data_hora": f"{datetime.now():%d/%m/%Y %H:%M:%S}", "codigo": t["codigo"], "placa": t["placa"],
           "analista": corta(quem if quem and quem != "local" else dados.get("analista"), 60), "texto": corta(dados.get("texto"), 4000),
           "campos_errados": [{k: corta(c.get(k), 300) for k in ("nome", "rotulo", "status", "sugerido", "no_card")}
                              for c in (dados.get("campos_errados") or [])[:40]],
           "preenchidos": {corta(k, 40): corta(v, 300) for k, v in list((dados.get("preenchidos") or {}).items())[:40]}}
    arquivo = Path(t["pasta"]) / "observacoes.json"
    _gravar_no_banco(obs, t["pasta"])
    with _trava:
        todas = (json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else []) + [obs]
        arquivo.write_text(json.dumps(todas, ensure_ascii=False, indent=1), encoding="utf-8")
        novo = not OBSERVACOES.exists()
        with OBSERVACOES.open("a", encoding="utf-8-sig" if novo else "utf-8", newline="") as f:
            w = csv.writer(f, delimiter=";")
            if novo:
                w.writerow(["data_hora", "codigo", "placa", "analista", "observacao", "campos_errados", "preenchidos_pela_ia", "pasta"])
            w.writerow([obs["data_hora"], obs["codigo"], obs["placa"], obs["analista"], obs["texto"],
                        " | ".join(f"{c['rotulo']}: sugerido {c['sugerido'] or '-'} (card {c['no_card'] or '-'})" for c in obs["campos_errados"]),
                        " | ".join(f"{k}={v}" for k, v in obs["preenchidos"].items()), t["pasta"]])
    return todas


# ---------------- administração (/admin) ----------------
# Senha em admin_senha= no .env (lida a cada pedido). Sem ela a administração fica desligada.

FOTO_LOGIN = re.compile(r"^login_falhou-\d{8}-\d{6}\.png$")


def _senha_admin():
    return config._ler_env().get("admin_senha", "")


def revogar_chave(chave):
    with _trava:
        todas = analistas()
        nome = todas.pop(chave, None)
        ANALISTAS.write_text(json.dumps(todas, ensure_ascii=False, indent=1), encoding="utf-8")
    return nome


def _custo_do_dia():
    """R$ gastos com IA hoje (custos_ia.csv) e quantas chamadas."""
    hoje, total, n = f"{datetime.now():%d/%m/%Y}", 0.0, 0
    try:
        with custo_ia.ARQUIVO.open(encoding="utf-8-sig", newline="") as f:
            for linha in csv.DictReader(f, delimiter=";"):
                if (linha.get("data_hora") or "").startswith(hoje):
                    n += 1
                    try:
                        total += float((linha.get("custo_brl") or "0").replace(",", "."))
                    except ValueError:
                        pass
    except OSError:
        pass
    return {"reais": round(total, 4), "chamadas": n}


def situacao_admin():
    with _trava:
        trabalhos = [{"pedido": k, **{c: v.get(c) for c in ("estado", "etapa", "erro", "placa", "codigo", "por", "inicio", "fim")}}
                     for k, v in TRABALHOS.items()]
    from . import sessao

    hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    sessao_arq = sessao.arquivo_sessao()
    fotos = sorted((f.name for f in LOGS.glob("login_falhou-*.png")), reverse=True)[:5] if LOGS.exists() else []
    return {
        "versao": VERSAO,
        "analistas": [{"nome": n, "chave": c} for c, n in sorted(analistas().items(), key=lambda x: x[1].lower())],
        "fila": sorted((t for t in trabalhos if t["estado"] == "fila"), key=lambda t: t.get("inicio") or 0),
        "rodando": next((t for t in trabalhos if t["estado"] == "rodando"), None),
        "prontos_hoje": sum(1 for t in trabalhos if t["estado"] == "pronto" and (t.get("fim") or 0) >= hoje),
        "erros": sorted((t for t in trabalhos if t["estado"] == "erro"), key=lambda t: -(t.get("fim") or t.get("inicio") or 0))[:20],
        "custo_hoje": _custo_do_dia(),
        "sessao_crm": {"existe": sessao_arq.exists(),
                       "salva_em": f"{datetime.fromtimestamp(sessao_arq.stat().st_mtime):%d/%m/%Y %H:%M}" if sessao_arq.exists() else None,
                       "empresa": sessao.EMPRESA, "login_automatico": sessao.tem_credenciais(), "sem_tela": sessao.SEM_TELA},
        "fotos_login": fotos,
        "auto": situacao_auto(),
        "automaticos_hoje": sum(1 for t in trabalhos if t["estado"] == "pronto" and (t.get("fim") or 0) >= hoje
                                and "automático" in (t.get("por") or [])),
    }


def observacoes_admin(limite=500):
    """As observações mais recentes primeiro, do banco."""
    with _banco() as db:
        linhas = db.execute("SELECT data_hora, codigo, placa, analista, texto, campos_errados, preenchidos FROM observacoes "
                            "ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
    resumo_campos = lambda cs: " | ".join(f"{c.get('rotulo')}: sugerido {c.get('sugerido') or '-'} (card {c.get('no_card') or '-'})" for c in cs)
    saida = []
    for data_hora, codigo, placa, analista, texto, campos, preench in linhas:
        try:
            campos, preench = json.loads(campos or "[]"), json.loads(preench or "{}")
        except ValueError:
            campos, preench = [], {}
        saida.append({"data_hora": data_hora, "codigo": codigo, "placa": placa, "analista": analista, "observacao": texto,
                      "campos_errados": resumo_campos(campos) if isinstance(campos, list) else str(campos),
                      "preenchidos_pela_ia": " | ".join(f"{k}={v}" for k, v in preench.items()) if isinstance(preench, dict) else str(preench)})
    return saida


def log_admin(linhas=300):
    arquivo = LOGS / f"assistente-{datetime.now():%Y%m%d}.log"
    try:
        with arquivo.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 200_000))
            texto = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(texto.splitlines()[-linhas:])


# ---------------- servidor ----------------

class Pedido(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _enviar(self, corpo, tipo="application/json", status=200):
        dados = corpo if isinstance(corpo, bytes) else json.dumps(corpo, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", tipo + ("; charset=utf-8" if tipo.startswith(("text", "application/json")) else ""))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Versao", str(VERSAO))
        self.end_headers()
        self.wfile.write(dados)

    def _quem(self):
        """Nome de quem chama, ou None (proibido). Sempre exige o cabeçalho do assistente (outro site não manda sem CORS).
        Pela rede: só com chave válida. No próprio PC (127.0.0.1, Host local contra outro domínio apontado para cá):
        a chave é opcional e, sem ela, o nome é "local"."""
        if self.headers.get(CABECALHO) != "1":
            return None
        nome = analistas().get(self.headers.get(CHAVE) or "")
        if nome:
            return nome
        host = (self.headers.get("Host") or "").split(":")[0]
        local = self.client_address[0] in ("127.0.0.1", "::1") and host in ("127.0.0.1", "localhost")
        return "local" if local else None

    def _proibido(self):
        motivo = ("chave do analista ausente ou inválida: abra as opções da extensão e cole a sua chave"
                  if self.headers.get(CABECALHO) == "1" else "proibido")
        return self._enviar({"erro": motivo, "proibido": True}, status=403)

    def _admin_ok(self):
        """A senha vem no cabeçalho X-Admin (cabeçalho próprio: outro site não consegue mandar sem CORS).
        Senha errada espera 1 s, para não dar para chutar rápido."""
        import hmac

        senha = _senha_admin()
        if not senha:
            self._enviar({"erro": "administração desligada: coloque admin_senha= no .env do servidor", "desligada": True}, status=503)
            return False
        if not hmac.compare_digest((self.headers.get("X-Admin") or "").encode(), senha.encode()):
            time.sleep(1)
            self._enviar({"erro": "senha errada", "proibido": True}, status=403)
            return False
        return True

    def _admin_get(self, caminho, q):
        if not self._admin_ok():
            return
        if caminho == "/api/admin/situacao":
            return self._enviar(situacao_admin())
        if caminho == "/api/admin/observacoes":
            return self._enviar(observacoes_admin())
        if caminho == "/api/admin/log":
            return self._enviar({"texto": log_admin()})
        m = re.match(r"^/api/admin/foto/(.+)$", caminho)
        if m and FOTO_LOGIN.match(m.group(1)) and (LOGS / m.group(1)).exists():
            return self._enviar((LOGS / m.group(1)).read_bytes(), "image/png")
        return self._enviar({"erro": "não encontrado"}, status=404)

    def _admin_post(self, caminho, corpo):
        if not self._admin_ok():
            return
        try:
            dados = json.loads(corpo or b"{}")
        except ValueError:
            return self._enviar({"erro": "JSON inválido"}, status=400)
        if caminho == "/api/admin/chave":
            nome = str(dados.get("nome") or "").strip()
            if not nome:
                return self._enviar({"erro": "escreva o nome do analista"}, status=400)
            chave = nova_chave(nome)
            print(f"{datetime.now():%H:%M:%S} · admin criou a chave de {nome}")
            return self._enviar({"ok": True, "nome": nome[:60], "chave": chave})
        if caminho == "/api/admin/auto":
            ligar_auto(bool(dados.get("ligado")))
            return self._enviar({"ok": True, "auto": situacao_auto()})
        if caminho == "/api/admin/revogar":
            nome = revogar_chave(str(dados.get("chave") or ""))
            if nome is None:
                return self._enviar({"erro": "chave não existe"}, status=404)
            print(f"{datetime.now():%H:%M:%S} · admin revogou a chave de {nome}")
            return self._enviar({"ok": True, "nome": nome})
        return self._enviar({"erro": "não encontrado"}, status=404)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._enviar((Path(__file__).parent / "assistente.html").read_bytes(), "text/html")
        if self.path in ("/admin", "/admin/"):
            return self._enviar((Path(__file__).parent / "admin.html").read_bytes(), "text/html")
        if self.path.startswith("/api/admin/"):
            caminho, _, q = self.path.partition("?")
            return self._admin_get(caminho, q)
        quem = self._quem()
        if not quem:
            return self._proibido()
        if self.path == "/api/quem":  # a tela de opções da extensão testa a conexão aqui
            return self._enviar({"analista": quem, "versao": VERSAO, **posicao(None)})
        if self.path == "/api/fila":
            with _trava:
                return self._enviar(sorted(({"pedido": k, **v} for k, v in TRABALHOS.items()), key=lambda x: -x.get("inicio", 0)))
        m = re.match(r"^/api/resultado/([\w-]+)$", self.path)
        if m:
            res = resultado(m.group(1))
            if not res:
                chave = m.group(1)  # ainda na fila/rodando? a extensão mostra "analisando..."
                with _trava:
                    pedido, t = next(((k, dict(v)) for k, v in TRABALHOS.items() if chave in (k, v.get("codigo"), v.get("placa"))),
                                     (None, {}))
                # nomes das fotos e telas: a extensão mostra as imagens do card sendo analisadas (/api/miniatura e /api/tela)
                return self._enviar({"erro": "card não analisado", "estado": t.get("estado"), "etapa": t.get("etapa"),
                                     "fotos": len(t.get("fotos") or []), "vistas": len(t.get("vistas") or []),
                                     "lidos": len(t.get("lidos") or []), "pedido": pedido, "inicio": t.get("inicio"),
                                     "fase": t.get("fase"), "nomes_fotos": t.get("fotos") or [], "nomes_vistos": t.get("vistas") or [],
                                     "nomes_lidos": t.get("lidos") or [], "telas": [x["titulo"] for x in t.get("telas") or []],
                                     "por": t.get("por") or [], **posicao(pedido)},
                                    status=404)
            res.pop("_imagens", None)
            return self._enviar(res)
        m = re.match(r"^/api/tela/([\w-]+)/(\d+)$", self.path)
        if m:
            try:
                foto = tela(m.group(1), int(m.group(2)))
            except Exception:
                foto = None
            if foto:
                return self._enviar(foto, "image/jpeg")
        m = re.match(r"^/api/miniatura/([\w-]+)/(\d+)$", self.path)
        if m:
            foto = miniatura(m.group(1), int(m.group(2)))
            if foto:
                return self._enviar(foto, "image/jpeg")
        m = re.match(r"^/api/imagem/([\w-]+)\?(.+)$", self.path)
        if m:  # só fotos que fazem parte do resultado daquele card (mini=1: reduzida, para a faixa da central de fotos)
            from urllib.parse import parse_qs

            q = parse_qs(m.group(2))
            res = resultado(m.group(1))
            caminho = (res or {}).get("_imagens", {}).get((q.get("r") or [""])[0])
            if caminho and Path(caminho).exists():
                if q.get("mini"):
                    foto = reduzida(caminho)
                    if foto:
                        return self._enviar(foto, "image/jpeg")
                return self._enviar(Path(caminho).read_bytes(), "image/jpeg" if caminho.lower().endswith((".jpg", ".jpeg")) else "image/png")
        return self._enviar({"erro": "não encontrado"}, status=404)

    def do_POST(self):
        if self.path.startswith("/api/admin/"):
            tamanho = int(self.headers.get("Content-Length", 0))
            if tamanho < 0 or tamanho > 10_000:
                return self._enviar({"erro": "grande demais"}, status=413)
            return self._admin_post(self.path, self.rfile.read(tamanho))
        quem = self._quem()
        if not quem:
            return self._proibido()
        if self.path not in ("/api/analisar", "/api/observacao"):
            return self._enviar({"erro": "não encontrado"}, status=404)
        tamanho = int(self.headers.get("Content-Length", 0))
        if tamanho < 0 or tamanho > 200_000:
            return self._enviar({"erro": "grande demais"}, status=413)
        corpo = self.rfile.read(tamanho)
        if self.path == "/api/observacao":
            try:
                return self._enviar({"ok": True, "observacoes": observar(json.loads(corpo), quem)})
            except (ValueError, AttributeError, TypeError) as e:
                return self._enviar({"erro": str(e)[:200]}, status=400)
        try:
            pedido = re.sub(r"[^\w-]", "", json.loads(corpo)["codigo"])[:20]
        except (ValueError, KeyError, TypeError):
            return self._enviar({"erro": "mande {\"codigo\": \"...\"}"}, status=400)
        if not pedido:
            return self._enviar({"erro": "código vazio"}, status=400)
        with _trava:
            t = TRABALHOS.get(pedido, {})
            ja_estava = t.get("estado") in ("fila", "rodando")
            por = (t.get("por") or []) if ja_estava else []
        if ja_estava:  # outro analista já pediu o mesmo card: roda uma vez só e os dois veem
            if quem not in por:
                _atualizar(pedido, por=por + [quem])
            return self._enviar({"ok": True, "ja_estava": True, **posicao(pedido)})
        _atualizar(pedido, estado="fila", etapa="", erro="", inicio=time.time(), por=[quem])
        FILA.put(pedido)
        print(f"{datetime.now():%H:%M:%S} · {quem} pediu {pedido}")
        return self._enviar({"ok": True, **posicao(pedido)})


LOGS = config.PASTA_SAIDA / "logs"


class _Log:
    """Copia o terminal para robo/saida/logs/assistente-AAAAMMDD.log (um arquivo por dia, com hora em cada linha)."""

    def __init__(self, original):
        self.original, self.inicio_linha, self._trava = original, True, threading.Lock()

    def write(self, texto):
        try:
            self.original.write(texto)
        except (OSError, ValueError, AttributeError):  # servidor sem terminal (Agendador de Tarefas): só o arquivo
            pass
        with self._trava:
            agora = datetime.now()
            linhas = texto.splitlines(keepends=True)
            saida = "".join((f"{agora:%H:%M:%S} " if self.inicio_linha or i else "") + l for i, l in enumerate(linhas))
            if linhas:
                self.inicio_linha = linhas[-1].endswith(("\n", "\r"))
            try:
                LOGS.mkdir(parents=True, exist_ok=True)
                with (LOGS / f"assistente-{agora:%Y%m%d}.log").open("a", encoding="utf-8") as f:
                    f.write(saida)
            except OSError:
                pass
        return len(texto)

    def flush(self):
        try:
            self.original.flush()
        except (OSError, ValueError, AttributeError):
            pass

    def __getattr__(self, nome):
        return getattr(self.original, nome)


def main():
    for fluxo in (sys.stdout, sys.stderr):  # saída redirecionada no Windows usa cp1252 e quebra com "→"/acentos
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.stdout, sys.stderr = _Log(sys.stdout), _Log(sys.stderr)
    ap = argparse.ArgumentParser()
    ap.add_argument("--importar", nargs="+", help="pasta(s) de demo já rodada: vira resultado sem abrir o CRM")
    ap.add_argument("--sem-navegador", action="store_true", help="não abre a página do assistente")
    ap.add_argument("--porta", type=int, default=PORTA, help=f"porta do servidor (padrão {PORTA}; a extensão usa {PORTA})")
    ap.add_argument("--rede", action="store_true", help="(padrão; mantido por compatibilidade) atende as outras máquinas da rede")
    ap.add_argument("--local", action="store_true", help="atende só este PC (127.0.0.1), sem a rede")
    ap.add_argument("--nova-chave", metavar="NOME", help="cria a chave de um analista, mostra e sai")
    ap.add_argument("--chaves", action="store_true", help="lista os analistas com chave e sai")
    args = ap.parse_args()
    if args.nova_chave:
        print(f"Chave de {args.nova_chave}: {nova_chave(args.nova_chave)}")
        print("Cole nas opções da extensão (chrome://extensions → Assistente → Detalhes → Opções da extensão).")
        return
    if args.chaves:
        for chave, nome in analistas().items():
            print(f"{nome}: {chave}")
        return
    carregar_indice()
    preparar_banco()
    if TRABALHOS:
        for t in TRABALHOS.values():  # o que estava rodando quando o servidor caiu
            if t.get("estado") in ("fila", "rodando"):
                t.update(estado="erro", etapa="", erro="servidor reiniciado: analise de novo")
    for pasta in args.importar or []:
        importar(pasta)
    ThreadingHTTPServer.allow_reuse_address = False  # no Windows duas instâncias na mesma porta não dão erro sem isto
    rede = not args.local
    servidor = ThreadingHTTPServer(("0.0.0.0" if rede else "127.0.0.1", args.porta), Pedido)
    threading.Thread(target=_trabalhador, daemon=True).start()
    print(f"Assistente em http://127.0.0.1:{args.porta} · SÓ LEITURA no CRM · Ctrl+C para encerrar")
    if rede:
        print("Na rede: " + (" · ".join(f"http://{ip}:{args.porta}" for ip in ips_da_rede()) or "(IP não encontrado)"))
        print(f"   {len(analistas())} analista(s) com chave · crie na administração ou com --nova-chave \"Nome\"")
        print(f"   se as outras máquinas não conectarem: libere a porta {args.porta} no firewall desta máquina")
    print(f"Administração: http://{(ips_da_rede() or ['127.0.0.1'])[0] if rede else '127.0.0.1'}:{args.porta}/admin"
          + ("" if _senha_admin() else "  (DESLIGADA: coloque admin_senha= no .env)"))
    print("Extensão: chrome://extensions → Modo do desenvolvedor → Carregar sem compactação → pasta extensao/")
    if not args.sem_navegador:
        webbrowser.open(f"http://127.0.0.1:{args.porta}")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
