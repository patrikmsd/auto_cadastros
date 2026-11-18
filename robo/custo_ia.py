"""Custo de cada chamada de IA: mostra no terminal e grava em robo/saida/custos_ia.csv.

Preços em US$ por 1 milhão de tokens (entrada, entrada em cache, saída), da tabela pública de cada empresa.
Confira em openai.com/api/pricing e ai.google.dev/pricing de tempos em tempos e ajuste aqui.
Cotação do dólar: "dolar" no .env (padrão 5,40).
"""
import csv
import re
from datetime import datetime

from . import config

PRECOS = {
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "gpt-4o": (2.50, 1.25, 10.00),
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1": (2.00, 0.50, 8.00),
    "gpt-5-nano": (0.05, 0.005, 0.40),
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "gpt-5": (1.25, 0.125, 10.00),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),  # preço de 30/07/2026
    "gpt-6-luna": (0.10, 0.01, 0.50),  # não confirmado na página da OpenAI: conferir na fatura
    "gpt-6-sol": (2.00, 0.20, 10.00),  # idem (fontes de terceiros, out/2026)
}
PRECOS_PAGINA = {  # OCR cobrado por página: US$ por 1.000 páginas (só texto, com extração estruturada)
    "mistral-ocr": (4.00, 5.00),  # OCR 4/4.1, fontes de terceiros (nov/2026): conferir em mistral.ai/pricing
}
GRATIS = ("gemini",)  # faixa grátis do Google AI Studio: custo zero, mas registra os tokens
ARQUIVO = config.PASTA_SAIDA / "custos_ia.csv"
DOLAR = float(config.ENV.get("dolar", "5.40").replace(",", "."))
DESTA_EXECUCAO = []  # (tarefa, modelo, US$) de cada chamada feita nesta execução


def _preco(modelo):
    """'gpt-4o-mini-2024-07-18' → preço do 'gpt-4o-mini'. Só tira a data do fim: 'gpt-5.6-luna' não vira 'gpt-5'."""
    return PRECOS.get(re.sub(r"-\d{4}-\d{2}-\d{2}$", "", modelo))


def registrar(tarefa, modelo, entrada, saida, cache=0, raciocinio=0):
    """entrada inclui o cache; raciocínio já vem somado na saída (OpenAI) ou à parte (Gemini). Devolve US$."""
    preco = _preco(modelo)
    if preco:
        e, c, s = preco
        dolares = ((entrada - cache) * e + cache * c + (saida + raciocinio) * s) / 1_000_000
        texto = f"US$ {dolares:.5f} (≈ R$ {dolares * DOLAR:.4f})"
    elif modelo.startswith(GRATIS):
        dolares, texto = 0.0, "grátis (faixa gratuita)"
    else:
        dolares, texto = None, "preço desconhecido: inclua o modelo em PRECOS (robo/custo_ia.py)"
    print(f"   💲 {tarefa} · {modelo} · tokens: entrada {entrada} (cache {cache}) · saída {saida}"
          + (f" · raciocínio {raciocinio}" if raciocinio else "") + f" · {texto}")
    novo = not ARQUIVO.exists()
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    with ARQUIVO.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if novo:
            w.writerow(["data_hora", "tarefa", "modelo", "tokens_entrada", "tokens_cache", "tokens_saida",
                        "tokens_raciocinio", "custo_usd", "custo_brl"])
        w.writerow([f"{datetime.now():%d/%m/%Y %H:%M:%S}", tarefa, modelo, entrada, cache, saida, raciocinio,
                    "" if dolares is None else f"{dolares:.6f}".replace(".", ","),
                    "" if dolares is None else f"{dolares * DOLAR:.6f}".replace(".", ",")])
    DESTA_EXECUCAO.append((tarefa, modelo, dolares))
    return dolares


def registrar_paginas(tarefa, modelo, paginas, estruturado=False):
    """Mesmo registro, para serviço cobrado por página. Grava as páginas na coluna de tokens de entrada. Devolve US$."""
    preco = next((p for nome, p in PRECOS_PAGINA.items() if modelo.startswith(nome)), None)
    dolares = paginas * preco[estruturado] / 1000 if preco else None
    texto = f"US$ {dolares:.5f} (≈ R$ {dolares * DOLAR:.4f})" if preco else "preço desconhecido: inclua em PRECOS_PAGINA"
    print(f"   💲 {tarefa} · {modelo} · {paginas} página(s){' com extração' if estruturado else ''} · {texto}")
    novo = not ARQUIVO.exists()
    with ARQUIVO.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if novo:
            w.writerow(["data_hora", "tarefa", "modelo", "tokens_entrada", "tokens_cache", "tokens_saida",
                        "tokens_raciocinio", "custo_usd", "custo_brl"])
        w.writerow([f"{datetime.now():%d/%m/%Y %H:%M:%S}", tarefa, f"{modelo} ({paginas} pág.)", paginas, 0, 0, 0,
                    "" if dolares is None else f"{dolares:.6f}".replace(".", ","),
                    "" if dolares is None else f"{dolares * DOLAR:.6f}".replace(".", ",")])
    DESTA_EXECUCAO.append((tarefa, modelo, dolares))
    return dolares


def total():
    """Soma desta execução em US$ (None se algum modelo não tiver preço)."""
    valores = [d for _, _, d in DESTA_EXECUCAO]
    return None if None in valores else sum(valores)
