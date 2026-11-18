"""Tabelas de preço (mensalidade Ouro/Prata/Bronze por faixa de valor) lidas dos PDFs na raiz do projeto.

O texto do PDF sai com as colunas uma depois da outra e misturado com os preços dos opcionais. Em cada página,
a coluna de um plano é a sequência crescente com o mesmo número de valores que as faixas; Ouro > Prata > Bronze.
Só usa a página quando acha as 3 colunas e Ouro − Prata e Prata − Bronze são constantes (confere a leitura).
"""
import json
import logging
import re

from . import config

PDFS = {"DENTRO": "VALLE TABELA PREÇO DENTRO*.pdf", "FORA": "VALLE TABELA PREÇO FORA*.pdf"}
CACHE = config.PASTA_SAIDA / "tabelas_preco.json"
FAIXA = re.compile(r"(?:Até\s*R\$\s*([\d.\s]+)|R\$\s*([\d.\s]+?)\s*à\s*R\$\s*([\d\s]+?)\s*M\s*I\s*L)")


def _reais(t):
    return float(t.replace(".", "").replace(",", "."))


def _faixas(texto):
    faixas = []
    for ate, de, a in FAIXA.findall(texto):
        if ate:
            faixas.append((0, int(re.sub(r"\D", "", ate))))
        else:
            faixas.append((int(re.sub(r"\D", "", de)), int(re.sub(r"\D", "", a)) * 1000))
    return faixas


def _colunas(precos, n):
    """Sequências crescentes de n preços seguidos; devolve (ouro, prata, bronze) ou None."""
    # sequências crescentes máximas; um opcional barato logo antes (ex.: R$25) entra no começo, então fica com o fim
    achadas, inicio = [], 0
    for i in range(1, len(precos) + 1):
        if i == len(precos) or precos[i] <= precos[i - 1]:
            if i - inicio >= n:
                achadas.append(precos[i - n:i])
            inicio = i
    for k in range(len(achadas) - 2):
        trio = sorted(achadas[k:k + 3], key=lambda c: -c[0])
        d1 = {round(o - p, 2) for o, p in zip(trio[0], trio[1])}
        d2 = {round(p - b, 2) for p, b in zip(trio[1], trio[2])}
        if len(d1) == 1 and len(d2) == 1:
            return trio
    return None


def ler_pdfs():
    import pypdf

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    tabelas = []
    for regional, padrao in PDFS.items():
        for arquivo in sorted(config.RAIZ.glob(padrao)):
            for n_pag, pag in enumerate(pypdf.PdfReader(arquivo).pages, 1):
                texto = pag.extract_text()
                faixas = _faixas(texto)
                if not faixas:
                    continue
                cols = _colunas([_reais(p) for p in re.findall(r"R\$\s?(\d{2,3},\d{2})", texto)], len(faixas))
                if not cols:
                    continue
                titulo = next((l.strip() for l in texto.splitlines() if re.match(r"\s*(VEÍCULOS LEVES|PICK-UPS|MOTOCICLETAS|CAMINH)", l)), "")
                tabelas.append({"regional": regional, "arquivo": arquivo.name, "pagina": n_pag, "titulo": titulo,
                                "faixas": [{"de": de, "ate": ate, "ouro": o, "prata": p, "bronze": b}
                                           for (de, ate), o, p, b in zip(faixas, *cols)]})
    CACHE.write_text(json.dumps(tabelas, ensure_ascii=False, indent=2), encoding="utf-8")
    return tabelas


def tabelas():
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else ler_pdfs()


def preco(regional, valor, pagina=1):
    """Mensalidade do PDF (ouro, prata, bronze) para a faixa do valor protegido. Página 1 = veículos leves."""
    for t in tabelas():
        if t["regional"] == regional and t["pagina"] == pagina:
            for f in t["faixas"]:
                if f["de"] <= valor <= f["ate"] or (f["de"] == 0 and valor <= f["ate"]):
                    return f, t
    return None, None


if __name__ == "__main__":
    for t in ler_pdfs():
        print(f"{t['regional']} pág {t['pagina']} '{t['titulo']}': {len(t['faixas'])} faixas · "
              f"1ª {t['faixas'][0]} · última {t['faixas'][-1]}")
