"""Teste: OCR local (RapidOCR) × Mistral OCR nos documentos e no chassi dos cards de amostra.

As fotos vão já na rotação certa (giro do gabarito.json de cada card), para os dois lados.
Duas medidas por campo:
  texto    → o valor certo aparece no texto lido? (qualidade do OCR em si, igual para os dois)
  extração → o Mistral preencheu o campo certo? (extração estruturada dele, cobrada à parte)
Dados pessoais aparecem só como ✅/❌. Uso: python -m robo.comparar_mistral [--cards PDG4E67 ...] [--sem-extracao]
Sem --cards, roda todo card de testes/amostras que tenha gabarito.json (e o chassi do PKD6E70).
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor

import cv2

from . import config, custo_ia
from .comparar_ocr_llm import norm
from .mistral import ocr_mistral
from .ocr_teste import ler_imagem

GIROS = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
PESSOAIS = {"nome", "cpf", "nascimento", "validade", "registro_cnh", "endereco", "bairro", "cep"}
CURTOS = {"categoria"}  # "D", "AB": aparecem em qualquer texto, não entram na medida "texto"
INSTRUCAO = ("Documento de cadastro de proteção veicular (CRLV, CNH, comprovante de endereço ou foto do número do chassi "
             "gravado). Copie exatamente o que está escrito; deixe vazio o que não estiver no documento ou não der para "
             "ler com certeza, e liste esses campos em campos_duvidosos. Datas em dd/mm/aaaa. Em endereco, o logradouro "
             "com número e complemento como está escrito.")
CAMPOS = ["placa", "renavam", "chassi", "motor", "marca_modelo", "ano_fabricacao", "ano_modelo", "combustivel",
          "observacoes", "nome", "cpf", "nascimento", "validade", "registro_cnh", "categoria", "endereco", "bairro",
          "cidade", "cep"]
ESQUEMA = {"type": "object", "properties": {
    "tipo_documento": {"type": "string", "enum": ["CRLV", "CNH", "comprovante de endereço", "chassi gravado", "outro"]},
    **{c: {"type": "string"} for c in CAMPOS},
    "campos_duvidosos": {"type": "array", "items": {"type": "string"}}}, "required": ["tipo_documento"]}


def casos(cards=None):
    """[(rótulo, imagem já em pé, tipo, gabarito)] dos cards de amostra + o chassi do PKD6E70 (só sobrou a foto desvirada)."""
    lista = []
    todos = sorted(p.parent.name for p in config.PASTA_AMOSTRAS.glob("*/gabarito.json"))
    for card in cards or todos:
        gab = json.loads((config.PASTA_AMOSTRAS / card / "gabarito.json").read_text(encoding="utf-8"))
        for arquivo, g in gab.items():
            if arquivo.startswith("_"):
                continue
            img = ler_imagem(config.PASTA_AMOSTRAS / card / arquivo)
            if GIROS[g["giro"]] is not None:
                img = cv2.rotate(img, GIROS[g["giro"]])
            lista.append((f"{card} {g['tipo']}", img, g["tipo"], g["campos"]))
    if cards and "PKD6E70" not in cards:
        return lista
    cot = {x["nome"]: x["valor"] for x in json.loads((config.PASTA_SAIDA / "20261029-094635/PKD6E70/card_cotacoes.json")
                                                     .read_text(encoding="utf-8")) if x["nome"]}
    lista.append(("PKD6E70 chassi", ler_imagem(config.PASTA_SAIDA / "chassi_desvirado.jpg"), "chassi",
                  {"chassi": cot["vhclChassi"]}))
    return lista


def confere(campo, esperado, lido):
    e, l = norm(esperado), norm(lido)
    if not l:
        return False
    if campo in ("observacoes", "marca_modelo", "endereco", "nome"):
        return all(norm(p) in l for p in str(esperado).split())  # todas as palavras presentes
    if campo == "chassi":
        return l.removesuffix("REM") == e
    return l == e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sem-extracao", action="store_true", help="só o texto do Mistral (mais barato)")
    ap.add_argument("--cards", nargs="+", help="placas dos cards (padrão: todos com gabarito.json)")
    args = ap.parse_args()
    lista = casos(args.cards)

    from rapidocr_onnxruntime import RapidOCR

    print(f"▶ OCR local em {len(lista)} imagens (sequencial, como no robô)...")
    motor, local = RapidOCR(), {}
    t0 = time.time()
    for rotulo, img, *_ in lista:
        t = time.time()
        local[rotulo] = (" ".join(r[1] for r in (motor(img)[0] or [])), time.time() - t)
    tempo_local = time.time() - t0

    print(f"▶ Mistral OCR nas mesmas {len(lista)} imagens (em paralelo)...")
    esquema = None if args.sem_extracao else ESQUEMA

    def um(caso):
        rotulo, img, *_ = caso
        t = time.time()
        try:
            texto, extraido = ocr_mistral(f"comparação OCR local×Mistral · {rotulo}", img, esquema, INSTRUCAO)
        except SystemExit as e:
            texto, extraido = "", {"erro": str(e)}
        return rotulo, (texto, extraido or {}, time.time() - t)

    t0 = time.time()
    with ThreadPoolExecutor(6) as ex:
        mistral = dict(ex.map(um, lista))
    tempo_mistral = time.time() - t0
    custo = sum(d or 0 for _, m, d in custo_ia.DESTA_EXECUCAO)

    placar = {"local texto": [0, 0], "mistral texto": [0, 0], "mistral extração": [0, 0]}
    print("\n" + "═" * 110)
    for rotulo, _, tipo, campos in lista:
        txt_l, seg_l = local[rotulo]
        txt_m, ext, seg_m = mistral[rotulo]
        print(f"\n■ {rotulo}   (local {seg_l:.1f}s · Mistral {seg_m:.1f}s)")
        if "erro" in ext:
            print(f"   ⚠ {ext['erro']}")
        if tipo == "vazio":
            print(f"   imagem sem texto · local leu {len(txt_l)} caracteres · Mistral leu {len(txt_m.strip())} caracteres"
                  f"{' · extração inventou: ' + ', '.join(k for k, v in ext.items() if v and k in CAMPOS) if any(ext.get(c) for c in CAMPOS) else ''}")
            continue
        for campo, esperado in campos.items():
            linha = f"   {campo:<15}"
            if campo not in CURTOS:
                for nome, texto in (("local texto", txt_l), ("mistral texto", txt_m)):
                    ok = norm(esperado) in norm(texto)
                    placar[nome][0] += ok
                    placar[nome][1] += 1
                    linha += f" │ {nome} {'✅' if ok else '❌'}"
            else:
                linha += f" │ {'(curto: só na extração)':<33}"
            if not args.sem_extracao:
                lido = ext.get(campo, "")
                ok = confere(campo, esperado, lido)
                placar["mistral extração"][0] += ok
                placar["mistral extração"][1] += 1
                linha += f" │ extração {'✅' if ok else '❌'}" + ("" if campo in PESSOAIS else f" {str(lido)[:24]}")
            print(linha + ("" if campo in PESSOAIS else f"  │ certo: {esperado}"))
        if ext.get("campos_duvidosos"):
            print(f"     Mistral disse ter dúvida em: {', '.join(map(str, ext['campos_duvidosos']))}")

    print("\n" + "═" * 110)
    for nome, (a, t) in placar.items():
        if t:
            print(f"   {nome:<18} {a}/{t} ({a / t:.0%})")
    print(f"\n   tempo: local {tempo_local:.0f}s (sequencial) · Mistral {tempo_mistral:.0f}s ({len(lista)} imagens em paralelo, "
          f"média {sum(v[2] for v in mistral.values()) / len(lista):.1f}s cada)")
    print(f"   custo Mistral: US$ {custo:.4f} (≈ R$ {custo * custo_ia.DOLAR:.4f}) · R$ {custo * custo_ia.DOLAR / len(lista):.4f} por imagem"
          " · OCR local: zero e nada sai do computador")
    (config.PASTA_SAIDA / "comparacao_mistral.json").write_text(json.dumps(
        {r: {"local": local[r][0], "mistral_texto": mistral[r][0], "mistral_extracao": mistral[r][1]} for r in local},
        ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
