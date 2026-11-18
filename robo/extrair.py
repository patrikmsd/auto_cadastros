"""Tira campos de documentos com o OCR local (RapidOCR). Nada sai do computador.

CNH: cada campo tem um rótulo numerado ("4d CPF", "4b VALIDADE"...) e o valor logo abaixo; usa a posição das caixas
do OCR para ligar rótulo → valor, testando a foto em pé e deitada.
Comprovante: procura a linha do logradouro (RUA/AV/QD/LT) e a do bairro + cidade.
"""
import re

import cv2

from .ocr_teste import cpf_valido

DATA = r"\d{2}/\d{2}/\d{4}"
# campo → (regex do rótulo sem espaços, regex do valor)
ROTULOS_CNH = {
    "nascimento": (r"3DATA.*NASCIMENTO", DATA),
    "emissao": (r"4A?DATAEMISSAO", DATA),
    "validade": (r"4B?VALIDADE", DATA),
    "identidade": (r"4C?DOC.*IDENTIDADE", r"\d{5,}"),
    "cpf": (r"4D?CPF", r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}"),
    "registro": (r"5N.?REGISTRO", r"\d{9,11}"),
    "categoria": (r"9CAT", r"\b(ACC|A|B|C|D|E|AB|AC|AD|AE)\b"),
    "primeira_habilitacao": (r"^(1.?)?HABILITACAO$", DATA),
}
ORGAOS = r"(SSP|SDS|SESP|DETRAN|PC|DGPC|IFP|SEJUSP|SESDEC|POLICIA ?CIVIL)"


def _caixas(ocr, img):
    resultado, _ = ocr(img)
    return [{"x": b[0][0], "y": b[0][1], "x2": b[2][0], "y2": b[2][1], "t": t.strip()} for b, t, _ in (resultado or [])]


def _abaixo(rotulo, caixas, valor_re):
    """Primeira caixa logo abaixo do rótulo (até ~3 alturas), começando perto da mesma coluna, que case com o valor."""
    altura = max(rotulo["y2"] - rotulo["y"], 8)
    candidatas = [c for c in caixas if 0 < c["y"] - rotulo["y"] <= 3.5 * altura and abs(c["x"] - rotulo["x"]) < 6 * altura]
    for c in sorted(candidatas, key=lambda c: (c["y"], abs(c["x"] - rotulo["x"]))):
        m = re.search(valor_re, c["t"].upper())
        if m and c is not rotulo:
            return c["t"], m.group(0)
    return None, None


ROTULOS_CRLV = {"motor": (r"^MOTOR$", r"^[A-Z0-9][A-Z0-9-]{5,}$")}


def campos_crlv(ocr, img):
    """Campos do CRLV que o texto corrido não liga ao rótulo (ex.: número do motor)."""
    return campos_cnh(ocr, img, ROTULOS_CRLV)


def campos_cnh(ocr, img, rotulos=None):
    """Devolve {campo: valor} da CNH e quantos rótulos reconheceu. Só valores conferidos por formato."""
    rotulos = rotulos or ROTULOS_CNH
    melhor = ({}, 0)
    for g in (img, cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE), cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)):
        caixas = _caixas(ocr, g)
        campos, achados = {}, 0
        for campo, (rot_re, val_re) in rotulos.items():
            rotulo = next((c for c in caixas if re.search(rot_re, re.sub(r"[\s.ºª°]", "", c["t"].upper()))), None)
            if not rotulo:
                continue
            achados += 1
            texto, valor = _abaixo(rotulo, caixas, val_re)
            if not valor:
                continue
            if campo == "identidade":  # "70864798105SSP GO" → RG, órgão, UF
                m = re.search(r"(\d{5,})\s*" + ORGAOS + r"?\s*/?\s*([A-Z]{2})?", texto.upper())
                campos["rg"] = m.group(1)
                if m.group(2):
                    campos["orgao_expedidor"] = m.group(2) + (f"/{m.group(3)}" if m.group(3) else "")
            elif campo == "cpf":
                if cpf_valido(valor):
                    d = re.sub(r"\D", "", valor)
                    campos["cpf"] = f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
            else:
                campos[campo] = valor
        if achados > melhor[1]:
            melhor = (campos, achados)
    return melhor


PREFIXOS_BAIRRO = ("RESIDENCIAL", "JARDIM", "SETOR", "PARQUE", "VILA", "CONJUNTO", "CHACARA", "LOTEAMENTO", "BAIRRO",
                   "CONDOMINIO", "RESIDENCIA")


def campos_endereco(textos, cidade):
    """Logradouro, quadra/lote/número e bairro de um comprovante. O OCR gruda palavras: separa o que dá."""
    from .regras import sem_acento

    cidade = sem_acento(cidade).replace(" ", "")
    campos, linha_endereco = {}, None
    for i, t in enumerate(textos):
        u = sem_acento(t)
        if "logradouro" not in campos and "CNPJ" not in u and re.match(r"(RUA|R\.|AVENIDA|AV[.\s]|ALAMEDA|TRAVESSA|RODOVIA|ESTRADA)", u) and "," in u:  # "RUARIT.1,QD..."
            linha_endereco = i
            partes = [p.strip() for p in re.split(r",", u) if p.strip()]
            campos["logradouro"] = re.sub(r"\s+", " ", re.sub(r"^(RUA|AVENIDA|AV)\.?", r"\1 ", partes[0]).replace(".", " ")).strip()
            resto = ", ".join(partes[1:])
            numero = re.search(r"N[°ºO.]*\s*([A-Z]?\d+[A-Z]?)", resto)
            if numero:
                campos["numero"] = numero.group(1)
            quadra_lote = [p for p in partes[1:] if re.match(r"(QD|QUADRA|LT|LOTE)", p)]
            if quadra_lote:
                campos["complemento"] = " ".join(p.replace(".", " ").strip() for p in quadra_lote)
        colado = u.replace(" ", "")
        if "bairro" not in campos and cidade and colado.endswith(cidade) and len(colado) > len(cidade) + 3:
            bairro = colado[: -len(cidade)]
            for p in PREFIXOS_BAIRRO:
                if bairro.startswith(p) and len(bairro) > len(p):
                    bairro = p + " " + bairro[len(p):]
                    break
            campos["bairro"] = bairro
    # CEP só perto do endereço do cliente: o cabeçalho da conta traz o CEP da própria empresa (ex.: SANEAGO)
    if linha_endereco is not None:
        for t in textos[linha_endereco:linha_endereco + 3]:
            m = re.search(r"\b(\d{2})\.?(\d{3})-?(\d{3})\b", t)
            if m:
                campos["cep"] = f"{m.group(1)}{m.group(2)}-{m.group(3)}"
                break
    return campos


def _normal(texto):
    from .regras import sem_acento

    return re.sub(r"[^A-Z0-9]", "", re.sub(r"^(RUA|R|AVENIDA|AV|ALAMEDA|TRAVESSA)\b\.?", "", sem_acento(texto)))


def buscar_cep(uf, cidade, logradouro, bairro):
    """ViaCEP (público, grátis): procura a rua na cidade e só aceita se rua E bairro baterem com um único CEP.
    Envia só UF, cidade e nome da rua (nenhum dado da pessoa)."""
    import json
    import urllib.parse
    import urllib.request

    nome = re.sub(r"^(RUA|R|AVENIDA|AV|ALAMEDA|TRAVESSA)\.?\s+", "", logradouro.upper()).strip()
    alvo_rua, alvo_bairro = _normal(logradouro), _normal(bairro or "")
    # a busca do ViaCEP pede 3+ letras e não acha "RIT 1" com o número: tenta o nome inteiro e depois a 1ª palavra
    for termo in dict.fromkeys([nome, nome.split()[0] if nome else ""]):
        if len(termo) < 3:
            continue
        url = f"https://viacep.com.br/ws/{uf}/{urllib.parse.quote(cidade)}/{urllib.parse.quote(termo)}/json/"
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                resultados = json.loads(r.read())
        except Exception:
            return None
        if not isinstance(resultados, list):
            continue
        certos = {x["cep"] for x in resultados if _normal(x["logradouro"]) == alvo_rua
                  and (not alvo_bairro or _normal(x["bairro"]) == alvo_bairro)}
        if len(certos) == 1:
            return certos.pop()
    return None
