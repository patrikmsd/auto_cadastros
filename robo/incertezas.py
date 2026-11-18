"""Registro do que o robô leu SEM certeza: vai para revisão humana e para avaliar modelos/OCR depois.

Cada anotação: card, campo, valor lido, motivo da dúvida, fonte. Grava em robo/saida/incertezas.csv (acumula entre
execuções) e na pasta da execução (incertezas.json). Documento pessoal (CPF, RG, CNH) fica só com os 3 últimos dígitos.
"""
import csv
import json
import re
from datetime import date, datetime

from . import config

ARQUIVO = config.PASTA_SAIDA / "incertezas.csv"
ANOTADAS = []
PARECIDOS = str.maketrans("TFOQDIBSZG", "1700018526")  # o que OCR/IA confundem em número gravado no metal


def mascarar(valor):
    return re.sub(r"\d(?=(?:\D*\d){3})", "*", valor)


def anotar(card, campo, valor, motivo, fonte, sensivel=False):
    ANOTADAS.append({"data_hora": f"{datetime.now():%d/%m/%Y %H:%M:%S}", "card": card, "campo": campo,
                     "valor": mascarar(valor) if sensivel else valor, "motivo": motivo, "fonte": fonte})


def salvar(pasta_execucao=None):
    """Acrescenta as anotações desta execução ao CSV geral e grava incertezas.json na pasta da execução."""
    if not ANOTADAS:
        return
    novo = not ARQUIVO.exists()
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    with ARQUIVO.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(ANOTADAS[0]), delimiter=";")
        if novo:
            w.writeheader()
        w.writerows(ANOTADAS)
    if pasta_execucao:
        (pasta_execucao / "incertezas.json").write_text(json.dumps(ANOTADAS, ensure_ascii=False, indent=2), encoding="utf-8")


# ---- conferências que dão certeza (dígito verificador, coerência) ----

def renavam_valido(renavam):
    d = re.sub(r"\D", "", renavam).zfill(11)
    if len(d) != 11:
        return False
    soma = sum(int(n) * p for n, p in zip(d[:10], (3, 2, 9, 8, 7, 6, 5, 4, 3, 2)))
    dv = soma * 10 % 11
    return (0 if dv == 10 else dv) == int(d[10])


def cnh_valida(registro):
    d = re.sub(r"\D", "", registro)
    if len(d) != 11 or len(set(d)) == 1:
        return False
    n = list(map(int, d))
    dv1 = sum(x * p for x, p in zip(n[:9], range(9, 0, -1))) % 11
    desconto = 0
    if dv1 >= 10:
        dv1, desconto = 0, 2
    resto = sum(x * p for x, p in zip(n[:9], range(1, 10))) % 11
    dv2 = 0 if resto >= 10 else resto - desconto
    return [dv1, dv2] == n[9:] or [dv1, dv2 % 11] == n[9:]


def iguais_parecidos(a, b):
    """Iguais trocando só caracteres parecidos (1/T, 7/F, 0/O...): mesma leitura, provável erro de OCR."""
    a, b = a.upper().translate(PARECIDOS), b.upper().translate(PARECIDOS)
    return len(a) == len(b) and a == b


def datas_cnh_coerentes(dados):
    """nascimento < 1ª habilitação ≤ emissão < validade, e habilitação depois dos 18 anos."""
    def data(chave):
        try:
            d, m, a = map(int, dados[chave].split("/"))
            return date(a, m, d)
        except (KeyError, ValueError):
            return None
    nasc, prim, emis, val = (data(c) for c in ("nascimento", "primeira_habilitacao", "emissao", "validade"))
    ordem = [x for x in (nasc, prim, emis, val) if x]
    if ordem != sorted(ordem) or len(set(ordem)) != len(ordem) and emis != prim:
        return False
    return not (nasc and prim and (prim.year - nasc.year) < 18)
