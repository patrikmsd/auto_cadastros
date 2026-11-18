"""Certeza de verdade para o que a IA leu nos documentos (a "certeza" que a própria IA diz não vale como prova).

1. CONTAS: dígito do CPF e do registro da CNH, datas que fazem sentido (idade, emissão × validade, 1ª habilitação),
   CEP com 8 dígitos, categoria só com A–E, placa no formato antigo ou Mercosul. Conta que não fecha = a leitura está errada.
2. SEGUNDA LEITURA: o OCR local (RapidOCR, grátis, nada sai do computador) lê a MESMA imagem que a IA usou.
   Os dois chegaram ao mesmo valor → confirmado. Só a IA achou → incerteza (vai para o registro).
   A imagem que o OCR do lote não leu (CNH enviada no espaço das chaves) é lida aqui e guardada em ocr_extra.json.
"""
import json
import re
from datetime import date, datetime

from rapidfuzz import fuzz

from . import regras

# campo da tela → (documento, chave na leitura da IA). O RG é o CPF (RG não é mais usado).
CAMPOS = {"clntFullName": ("cnh", "nome_completo"), "clntRegistration": ("cnh", "cpf"), "clntRG": ("cnh", "cpf"),
          "clntExpdtDate": ("cnh", "data_emissao"), "clntCNH": ("cnh", "registro"), "clntfrstQual": ("cnh", "primeira_habilitacao"),
          "clntCNHExpir": ("cnh", "validade"), "clntBirthdate": ("cnh", "data_nascimento"), "clntCNHCategory": ("cnh", "categoria"),
          "clntExpdt": ("cnh", "orgao_expedidor"),
          "clntAddressZipcode": ("comprovante", "cep"), "clntAddress": ("comprovante", "logradouro"),
          "clntAddressNumber": ("comprovante", "numero"), "clntAddressNeighborhood": ("comprovante", "bairro"),
          "vhclClr": ("crlv", "cor"), "vhclFbrctnYrMdl": ("crlv", "ano_modelo"), "vhclBrand": ("crlv", "marca")}
FOTO_DO_DOC = {"cnh": "CNH do associado", "crlv": "CRLV do veículo", "comprovante": "comprovante de endereço"}


def _digitos(t):
    return re.sub(r"\D", "", str(t or ""))


def cnh_valida(registro):
    """Dígito verificador do nº de registro da CNH (conferido nos 5 gabaritos)."""
    n = _digitos(registro).zfill(11)
    if len(n) != 11 or n == n[0] * 11:
        return False
    desconto = 0
    dv1 = sum(int(n[i]) * (9 - i) for i in range(9)) % 11
    if dv1 >= 10:
        dv1, desconto = 0, 2
    x = sum(int(n[i]) * (1 + i) for i in range(9)) % 11
    return f"{dv1}{0 if x >= 10 else x - desconto}" == n[-2:]


def _data(t):
    try:
        return datetime.strptime(str(t or "").strip()[:10], "%d/%m/%Y").date()
    except ValueError:
        return None


def contas(doc, chave, valor, lido):
    """Erro de conta da leitura (texto) ou "" se fecha / não há conta para esse campo. lido = tudo que a IA leu do documento."""
    from .ocr_teste import cpf_valido

    if not valor:
        return ""
    hoje = date.today()
    if chave == "cpf":
        return "" if cpf_valido(_digitos(valor)) else "o dígito verificador do CPF não fecha"
    if chave == "registro":
        return "" if cnh_valida(valor) else "o dígito verificador do registro da CNH não fecha"
    if chave == "cep":
        return "" if len(_digitos(valor)) == 8 else "CEP não tem 8 dígitos"
    if chave == "categoria":
        return "" if re.fullmatch(r"[A-E]{1,5}", str(valor).upper().replace(" ", "")) else "categoria fora de A–E"
    if chave == "placa":
        return "" if re.fullmatch(r"[A-Z]{3}\d[A-Z0-9]\d{2}", re.sub(r"\W", "", str(valor).upper())) else "placa fora do formato"
    if chave in ("data_nascimento", "data_emissao", "validade", "primeira_habilitacao"):
        d = _data(valor)
        if not d:
            return "data fora do formato dd/mm/aaaa"
        nasc, emissao, primeira = (_data(lido.get(k)) for k in ("data_nascimento", "data_emissao", "primeira_habilitacao"))
        if chave == "data_nascimento" and not 18 <= (hoje - d).days / 365.25 <= 100:
            return "a idade não fica entre 18 e 100 anos"
        if chave == "data_emissao" and d > hoje:
            return "data de emissão no futuro"
        if chave == "validade" and emissao and d <= emissao:
            return "a validade não é depois da emissão"
        if chave == "primeira_habilitacao":
            if nasc and (d - nasc).days / 365.25 < 18:
                return "1ª habilitação antes dos 18 anos"
            if emissao and d > emissao:
                return "1ª habilitação depois da emissão"
    return ""


def _palavras(t):
    return [p for p in regras.sem_acento(str(t or "")).upper().replace(".", " ").split() if len(p) >= 2]


def no_ocr(valor, texto):

    if not texto or not valor:
        return None
    linhas = [regras.sem_acento(t).upper() for t in texto]
    d = _digitos(valor)
    if len(d) >= 8 and len(d) >= len(re.sub(r"\s", "", str(valor))) - 4:  # CPF, registro, datas, CEP
        return d in "".join(_digitos(t) for t in linhas) or any(d in _digitos(t) for t in linhas)
    palavras = _palavras(valor)
    if not palavras:
        return None
    soltas = {p for t in linhas for p in re.findall(r"[A-Z0-9]+", t)}
    colado = re.sub(r"[^A-Z0-9]", "", "".join(linhas))  # o OCR gruda palavras ("JOAOSILVA")
    # palavra curta ("AB", "VW") só vale solta: grudada ela aparece em qualquer texto
    return all(p in soltas or len(p) >= 4 and (p in colado or any(fuzz.ratio(p, s) >= 85 for s in soltas)) for p in palavras)


def ocr_da_imagem(pasta_card, rotulo, caminho, ocr):
    """Texto do OCR local da imagem: o que o lote já leu, ou lê agora (girando: documento de lado) e guarda."""
    if rotulo in ocr:
        return ocr[rotulo].get("texto") or []
    cache = pasta_card / "ocr_extra.json"
    extra = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    if rotulo not in extra:
        import cv2
        from rapidocr_onnxruntime import RapidOCR

        from .ocr_teste import ler_imagem

        img = ler_imagem(caminho)
        textos, leitor = [], RapidOCR()
        for g in ((img, cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE), cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE))
                  if img is not None else ()):
            lidas = [r[1] for r in (leitor(g)[0] or [])]
            if sum(map(len, lidas)) > sum(map(len, textos)):
                textos = lidas
        extra[rotulo] = textos
        cache.write_text(json.dumps(extra, ensure_ascii=False), encoding="utf-8")
    return extra[rotulo]


def checar(pasta_card, docs, imagens, ocr):
    """Para cada campo da tela lido pela IA: {"erro": conta que não fecha, "ocr": True (confirmou) / False / None}."""
    resultado = {}
    textos = {}
    for nome, (doc, chave) in CAMPOS.items():
        lido = docs.get(doc) or {}
        valor = lido.get(chave)
        rotulo = (docs.get("_fotos") or {}).get(FOTO_DO_DOC[doc])
        if not valor or not rotulo:
            continue
        if rotulo not in textos:
            try:
                textos[rotulo] = ocr_da_imagem(pasta_card, rotulo, imagens.get(rotulo), ocr)
            except Exception:
                textos[rotulo] = []
        resultado[nome] = {"erro": contas(doc, chave, valor, lido), "ocr": no_ocr(valor, textos[rotulo])}
    return resultado
