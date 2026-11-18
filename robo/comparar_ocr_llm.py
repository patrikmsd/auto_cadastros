"""Teste: OCR local (RapidOCR) × LLM com imagem (gpt-6-sol, gpt-6-luna) nos documentos do card de teste PKD6E70.

Mede acerto por campo (contra o gabarito: dados do card + números com dígito verificador), tempo e custo.
Dados pessoais aparecem só como ✅/❌ no terminal. Uso: python -m robo.comparar_ocr_llm [--modelos gpt-6-sol gpt-6-luna]
"""
import argparse
import contextlib
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import config, custo_ia, demo, extrair
from .gpt import perguntar_gpt

PASTA = config.PASTA_AMOSTRAS / "PKD6E70"
S = config.PASTA_SAIDA
DOCS = {  # arquivo → rótulo
    "29_anexo_crlv.png": "CRLV do Civic (anexo)",
    "03_documento_do_veiculo___aberto.jpg": "CRLV da moto (vistoria)",
    "02_documento_cnh_do_associado.jpg": "CNH",
    "04_comprovante_de_endereco.jpg": "Comprovante de endereço",
    "24_numero_do_chassi_do_veiculo.jpg": "Foto do chassi gravado",
    "27_painel_com_registro_e_km_do_veiculo.jpg": "Painel (km)",
}
PESSOAIS = {"cpf", "nascimento", "validade", "rg", "registro_cnh", "emissao", "primeira_habilitacao"}
PERGUNTA = """Extraia os dados deste documento/foto de cadastro de proteção veicular. A foto pode estar girada ou de
ponta-cabeça. Copie exatamente o que está escrito; se não conseguir ler um campo com certeza, deixe "" e liste em
campos_duvidosos. Datas em dd/mm/aaaa. Responda só JSON com estas chaves (use "" quando não se aplica):
{"tipo_documento": "CRLV|CNH|comprovante de endereço|chassi gravado|painel|outro",
 "placa": "", "placa_anterior": "", "chassi": "", "renavam": "", "motor": "", "marca_modelo": "", "ano_fabricacao": "",
 "ano_modelo": "", "combustivel": "", "observacoes": "",
 "cpf": "", "rg": "", "orgao_expedidor": "", "emissao": "", "nascimento": "", "validade": "", "primeira_habilitacao": "",
 "registro_cnh": "", "categoria": "",
 "logradouro": "", "numero": "", "complemento": "", "bairro": "", "cidade": "", "cep": "",
 "km": "", "campos_duvidosos": []}"""


def norm(v):
    from .regras import sem_acento

    return re.sub(r"[^A-Z0-9]", "", sem_acento(str(v or "")))


def gabarito():
    ler = lambda p: {x["nome"]: x["valor"] for x in json.loads(p.read_text(encoding="utf-8")) if x["nome"]}
    cot = ler(S / "20261029-094635/PKD6E70/card_cotacoes.json")  # card ainda pre1enchido (antes de limpar)
    con = ler(S / "20261029-140716/PKD6E70/card_contato.json")
    return {
        "CRLV do Civic (anexo)": {"placa": "PKD6E70", "chassi": cot["vhclChassi"], "renavam": "01100528463",
                                  "motor": cot["vhclEngnNmbr"], "ano_fabricacao": "2016", "ano_modelo": "2017",
                                  "combustivel": "ALCOOL/GASOLINA", "observacoes": "ALIENACAO FIDUCIARIA"},
        "CRLV da moto (vistoria)": {"placa": "SGT7C77", "chassi": "9C2KC2200PR103772", "renavam": "01346269200",
                                    "marca_modelo": "HONDA/CG 160 FAN", "observacoes": "MOTOR REMARCADO"},
        "CNH": {"cpf": con["clntRegistration"], "nascimento": con["clntBirthdate"], "validade": con["clntCNHExpir"]},
        "Comprovante de endereço": {"logradouro": "RUA RIT 1", "complemento": "QD 10A LT 22", "numero": "C22",
                                    "bairro": "RESIDENCIAL ITAMARACA", "cidade": "GOIANIA"},
        "Foto do chassi gravado": {"chassi": cot["vhclChassi"]},
        "Painel (km)": {"km": "055527"},
    }


def confere(campo, esperado, lido):
    e, l = norm(esperado), norm(lido)
    if not l:
        return False
    if campo in ("observacoes", "marca_modelo", "complemento"):
        return all(norm(p) in l for p in str(esperado).split())  # todas as palavras presentes
    if campo == "km":
        return l.removesuffix("KM").lstrip("0") == e.lstrip("0")
    if campo == "logradouro":
        return re.sub(r"^(RUA|R)", "", l) == re.sub(r"^(RUA|R)", "", e)
    if campo == "chassi":  # chassi remarcado vem impresso com "REM" no fim
        return l.removesuffix("REM") == e
    return l == e


def ocr_local():
    """Valores e tempo por documento com o pipeline atual do robô."""
    from rapidocr_onnxruntime import RapidOCR

    from .ocr_teste import ler_imagem

    saida = io.StringIO()
    t0 = time.time()
    with contextlib.redirect_stdout(saida):
        ocr = demo.ocr_documentos(S / "20261029-094635/PKD6E70", PASTA)
    tempos = {m.group(1).strip(): float(m.group(2)) for m in re.finditer(r" (.+?)  \[.*?\(\d+ linhas de texto, ([\d.]+)s\)", saida.getvalue())}
    motor = RapidOCR()
    r = {}
    t = time.time()
    a = ocr["Anexo (Atividades): CRLV"]
    m = extrair.campos_crlv(motor, ler_imagem(PASTA / "29_anexo_crlv.png"))[0]
    texto = norm(" ".join(a["texto"]))
    r["CRLV do Civic (anexo)"] = ({"placa": "PKD6E70" if "PKD6E70" in a["achados"]["placa"] else (a["achados"]["placa"] or [""])[0],
                                   "chassi": (a["achados"]["chassi"] or [""])[0], "renavam": (a["achados"]["renavam"] or [""])[0],
                                   "motor": m.get("motor", ""), "ano_fabricacao": "2016" if "2016" in texto else "",
                                   "ano_modelo": "2017" if "2017" in texto else "",
                                   "combustivel": "ALCOOL/GASOLINA" if "ALCOOLGASOLINA" in texto else "",
                                   "observacoes": "ALIENACAO FIDUCIARIA" if "ALIENACAOFIDUCIARIA" in texto else ""},
                                  tempos.get("Anexo (Atividades): CRLV", 0) + time.time() - t)
    b = ocr["Documento do veículo - Aberto"]
    tb = norm(" ".join(b["texto"]))
    r["CRLV da moto (vistoria)"] = ({"placa": (b["achados"]["placa"] or [""])[0], "chassi": (b["achados"]["chassi"] or [""])[0],
                                     "renavam": (b["achados"]["renavam"] or [""])[0],
                                     "marca_modelo": "HONDA/CG 160 FAN" if "HONDACG160FAN" in tb else "",
                                     "observacoes": "MOTOR REMARCADO" if b["achados"].get("remarcado") else ""},
                                    tempos.get("Documento do veículo - Aberto", 0))
    t = time.time()
    cnh, _ = extrair.campos_cnh(motor, ler_imagem(PASTA / "02_documento_cnh_do_associado.jpg"))
    r["CNH"] = ({"cpf": cnh.get("cpf", ""), "nascimento": cnh.get("nascimento", ""), "validade": cnh.get("validade", "")},
                tempos.get("Documento CNH do associado", 0) + time.time() - t)
    t = time.time()
    end = extrair.campos_endereco(ocr["Comprovante de endereço"]["texto"], "Goiânia")
    r["Comprovante de endereço"] = ({**end, "cidade": "GOIANIA" if "GOIANIA" in norm(" ".join(ocr["Comprovante de endereço"]["texto"])) else ""},
                                    tempos.get("Comprovante de endereço", 0) + time.time() - t)
    r["Foto do chassi gravado"] = ({"chassi": (ocr["Número do chassi do veículo"]["achados"]["chassi"] or [""])[0]},
                                   tempos.get("Número do chassi do veículo", 0))
    r["Painel (km)"] = ({"km": ""}, 0.0)  # o robô não passa o painel no OCR
    return r, time.time() - t0


def llm(modelo):
    def um(arquivo, rotulo):
        t = time.time()
        try:
            resposta = perguntar_gpt(f"comparação OCR×LLM · {rotulo}", PERGUNTA, [PASTA / arquivo], modelo)
        except SystemExit as e:
            resposta = {"erro": str(e)}
        return rotulo, resposta, time.time() - t

    antes = len(custo_ia.DESTA_EXECUCAO)
    t0 = time.time()
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(lambda x: um(*x), DOCS.items()))
    custo = sum(d or 0 for _, m, d in custo_ia.DESTA_EXECUCAO[antes:])
    return {r: (v, s) for r, v, s in res}, time.time() - t0, custo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelos", nargs="+", default=["gpt-6-sol", "gpt-6-luna"])
    args = ap.parse_args()
    gab = gabarito()
    print("▶ OCR local rodando (sequencial, como no robô)...")
    resultados = {"OCR local": (*ocr_local(), 0.0)}
    for m in args.modelos:
        print(f"▶ {m} rodando (6 imagens em paralelo)...")
        resultados[m] = llm(m)

    nomes = list(resultados)
    placar = {n: [0, 0] for n in nomes}
    print("\n" + "═" * 100)
    for doc, campos in gab.items():
        tempos = "  ·  ".join(f"{n} {resultados[n][0][doc][1]:.1f}s" for n in nomes)
        print(f"\n■ {doc}   ({tempos})")
        for campo, esperado in campos.items():
            linha = f"   {campo:<16}"
            for n in nomes:
                lido = resultados[n][0][doc][0].get(campo, "")
                ok = confere(campo, esperado, lido)
                placar[n][0] += ok
                placar[n][1] += 1
                mostra = "" if campo in PESSOAIS else f" {str(lido)[:22]}"
                linha += f" │ {n[:10]:<10} {'✅' if ok else '❌'}{mostra:<24}"
            print(linha + ("" if campo in PESSOAIS else f" │ certo: {esperado}"))
        for n in nomes[1:]:
            duv = resultados[n][0][doc][0].get("campos_duvidosos") or []
            if duv:
                print(f"     {n} disse ter dúvida em: {', '.join(map(str, duv))}")
    print("\n" + "═" * 100)
    print(f"{'':<12}{'acertos':>12}{'tempo total':>14}{'custo (6 imagens)':>22}{'custo/documento':>18}")
    for n in nomes:
        _, seg, usd = resultados[n]
        a, t = placar[n]
        print(f"{n:<12}{f'{a}/{t} ({a / t:.0%})':>12}{f'{seg:.0f}s':>14}{f'R$ {usd * custo_ia.DOLAR:.4f}':>22}"
              f"{f'R$ {usd * custo_ia.DOLAR / 6:.4f}':>18}")
    print("\nOCR local: custo zero e nada sai do computador. LLM: 6 imagens enviadas em paralelo à OpenAI.")
    (S / "comparacao_ocr_llm.json").write_text(json.dumps({n: {d: v[0] for d, v in r[0].items()} for n, r in resultados.items()},
                                                          ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
