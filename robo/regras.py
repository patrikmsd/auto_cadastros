"""Conferências feitas por código, sem IA. Cada uma devolve (status, detalhe).

status: OK · DIVERGENTE (erro no card) · ALERTA (precisa de olho humano) · NAO_VERIFICAVEL (falta dado)
"""
import json
import re
import unicodedata
from datetime import date

from openpyxl import load_workbook

from . import config

PLANILHA = config.RAIZ / "BASE REGRAS ROBO CADASTRO.xlsx"
UF = {"Acre": "AC", "Alagoas": "AL", "Amapá": "AP", "Amazonas": "AM", "Bahia": "BA", "Ceará": "CE", "Distrito Federal": "DF",
      "Espírito Santo": "ES", "Goiás": "GO", "Maranhão": "MA", "Mato Grosso": "MT", "Mato Grosso do Sul": "MS",
      "Minas Gerais": "MG", "Pará": "PA", "Paraíba": "PB", "Paraná": "PR", "Pernambuco": "PE", "Piauí": "PI",
      "Rio de Janeiro": "RJ", "Rio Grande do Norte": "RN", "Rio Grande do Sul": "RS", "Rondônia": "RO", "Roraima": "RR",
      "Santa Catarina": "SC", "São Paulo": "SP", "Sergipe": "SE", "Tocantins": "TO"}
UF_NEGADAS = {"AC", "AP", "AM", "PA", "RO", "RR"}
# DOC: motos em PE têm rastreador obrigatório, exceto nestas cidades
PE_ISENTAS = {"CARNAIBA", "FLORES", "TRIUNFO", "AFOGADOS DA INGAZEIRA", "TABIRA", "TRINDADE", "ARARIPINA", "BODOCO", "EXU",
              "GRANITO", "IPUBI", "MOREILANDIA", "OURICURI", "SANTA CRUZ", "SANTA FILOMENA", "PARNAMIRIM", "SALGUEIRO",
              "TERRA NOVA", "PETROLINA", "QUIXABA", "ITAPETIM", "SAO JOSE DO EGITO", "BREJINHO", "SERTANIA",
              "SANTA TEREZINHA", "SANTA CRUZ DO CAPIBARIBE", "TUPARETAMA"}
# fotos que precisam existir na vistoria (procura pelo começo do nome que o CRM mostra)
FOTOS_OBRIGATORIAS = ("Documento CNH", "Documento do veículo", "Comprovante de endereço", "Número do chassi",
                      "Frente", "Traseira", "Painel com registro e km")
FOTOS_DOCUMENTO = ("Documento CNH", "Documento do veículo", "Comprovante de endereço")


def sem_acento(texto):
    return unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().upper().strip()


def brl(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def reais(texto):
    m = re.search(r"R\$\s*([\d.]+,\d{2})", texto or "")
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def _regional_planilha():
    wb = load_workbook(PLANILHA, read_only=True, data_only=True)
    linhas = list(wb["REGIONAL"].iter_rows(min_row=2, values_only=True))
    return {(str(uf), sem_acento(str(cidade))): str(regra) for nivel, uf, cidade, regra in linhas if nivel == "CIDADE"}


VENCIMENTOS = next(iter(sorted(config.RAIZ.glob("VENCIMENTOS DAS ASSOCIA*.xlsx"))), None)


def vencimento_liberado(cooperativa, hoje=None):
    """Planilha VENCIMENTOS DAS ASSOCIAÇÕES: aba da associação (ANCORE, VALLE...), última DATA LIBERAÇÃO ≤ hoje.
    Devolve (dia "10"/"25", "25/11", data da linha) ou None."""
    if not VENCIMENTOS:
        return None
    hoje = hoje or date.today()
    wb = load_workbook(VENCIMENTOS, read_only=True, data_only=True)
    aba = next((n for n in wb.sheetnames if sem_acento(n) in sem_acento(cooperativa)), None)
    if not aba:
        return None
    escolhida = None
    for liberacao, venc, *_ in wb[aba].iter_rows(min_row=2, values_only=True):
        if liberacao is None or venc is None:
            continue
        if isinstance(liberacao, str):  # na aba ANCORE as datas estão como texto "10/01/2026"
            try:
                d, m, a = map(int, liberacao.strip().split("/"))
                liberacao = date(a, m, d)
            except ValueError:
                continue
        liberacao = liberacao.date() if hasattr(liberacao, "date") else liberacao
        if liberacao <= hoje and (escolhida is None or liberacao > escolhida[2]):
            escolhida = (str(venc).split("/")[0], str(venc), liberacao)
    return escolhida


PAGINA_PRECO = {"01": 1}  # código da tabela do CRM → página do PDF de preço (1 = VEÍCULOS LEVES). Outras: a mapear


def _conferir_mensalidade(card, valor, regional_cidade):
    from . import precos

    planos = card.get("_planos") or {}
    codigo = (re.match(r"(\d{2})", card.get("_tabela_no_card", "")) or [None, None])[1]
    if not planos or not valor:
        return ("NAO_VERIFICAVEL", "planos ou valor protegido não lidos no card")
    if codigo not in PAGINA_PRECO:
        return ("NAO_VERIFICAVEL", f"tabela {card.get('_tabela_no_card')}: preço ainda não mapeado no PDF")
    regional = "DENTRO" if regional_cidade.startswith("DENTRO") else "FORA"
    faixa, tabela = precos.preco(regional, valor, PAGINA_PRECO[codigo])
    if not faixa:
        return ("NAO_VERIFICAVEL", f"faixa de R$ {brl(valor)} não encontrada no PDF {regional}")
    difs = {p: round(planos[p] - faixa[p.lower()], 2) for p in ("OURO", "PRATA", "BRONZE") if p in planos}
    texto = " · ".join(f"{p.title()} R$ {brl(planos[p])} (PDF {brl(faixa[p.lower()])})" for p in difs)
    fonte = f"{tabela['arquivo']} pág. {tabela['pagina']}, faixa R$ {brl(faixa['de'])}–{brl(faixa['ate'])}"
    if all(d == 0 for d in difs.values()):
        return ("OK", f"{texto} · {fonte}")
    if len(set(difs.values())) == 1:  # mesma diferença em todos os planos: típico de ajuste dos gestores
        d = next(iter(difs.values()))
        return ("ALERTA", f"todos os planos R$ {brl(abs(d))} {'acima' if d > 0 else 'abaixo'} do PDF: ajuste não cadastrado "
                          f"na aba AJUSTES ou tabela desatualizada? {texto} · {fonte}")
    return ("DIVERGENTE", f"diferença varia entre os planos: {texto} · {fonte}")


def tipo(card):
    t = card["tipo_veiculo"].lower()
    return "moto" if "moto" in t else "caminhao" if "caminh" in t else "leve"


def cilindrada(card):
    """Vale o número do nome do modelo (CG 160 → 160), não a cc real."""
    modelo = re.sub(r"\b(19|20)\d{2}\b", "", card["veiculo"].split(",", 1)[-1])
    m = re.search(r"(?<![\d.,])(\d{2,4})(?![\d.,])", modelo)  # pega "750" em "NC 750X", ignora "1.0" e anos
    return int(m.group(1)) if m else None


def verificar(card, pasta_fotos=None, ler_fotos=True):
    r = {}
    valor = reais(card["valor_protegido"])
    fipe = reais(card["valor_fipe"])
    uf = UF.get(card["estado_circulacao"], card["estado_circulacao"])
    cidade = sem_acento(card["cidade_circulacao"])
    regional_cidade = _regional_planilha().get((uf, cidade), "")

    # 1. local de circulação autorizado
    if uf in UF_NEGADAS:
        r["regional_autorizada"] = ("DIVERGENTE", f"{uf}: estado não autorizado para cadastro")
    elif "NÃO COBERTA" in regional_cidade:
        r["regional_autorizada"] = ("DIVERGENTE", f"{card['cidade_circulacao']}/{uf}: {regional_cidade}")
    elif not uf:
        r["regional_autorizada"] = ("NAO_VERIFICAVEL", "estado de circulação não lido")
    else:
        r["regional_autorizada"] = ("OK", f"{card['cidade_circulacao']}/{uf}" + (f" · {regional_cidade}" if regional_cidade else ""))

    # 2. valor protegido × FIPE (depreciação de 30% só com motivo: sinistro, chassi remarcado, leilão)
    depreciado = card.get("_depreciacao") not in ("", "Selecione", None)
    if not (valor and fipe):
        r["valor_protegido"] = ("NAO_VERIFICAVEL", "valor FIPE ou protegido não lido")
    elif depreciado:
        r["valor_protegido"] = (("OK" if abs(valor - fipe * 0.7) < 1 else "ALERTA"),
                                f"depreciação '{card['_depreciacao']}': protegido R$ {brl(valor)} · FIPE R$ {brl(fipe)}")
    else:
        r["valor_protegido"] = (("OK" if abs(valor - fipe) < 1 else "DIVERGENTE"), f"protegido R$ {brl(valor)} · FIPE R$ {brl(fipe)}")

    # 2b. mensalidade de cada plano × tabela de preço (PDF) da regional, pela faixa do valor protegido
    r["mensalidade"] = _conferir_mensalidade(card, valor, regional_cidade)

    # 3. dia de vencimento
    dia = card.get("_dia_vencimento", "")
    liberado = vencimento_liberado(card.get("cooperativa", ""))
    regra = f"planilha: liberação desde {liberado[2]:%d/%m/%Y} → 1º vencimento {liberado[1]} (dia {liberado[0]})" if liberado else ""
    if dia in ("", "Selecione"):
        r["dia_vencimento"] = ("NAO_VERIFICAVEL", "não preenchido" + (f" · {regra}" if regra else ""))
    elif dia not in ("10", "25"):
        r["dia_vencimento"] = ("DIVERGENTE", f"dia {dia} (só 10 ou 25)")
    elif liberado and dia != liberado[0]:
        r["dia_vencimento"] = ("ALERTA", f"dia {dia} no card · {regra} · outro dia só com antecipação autorizada pela diretoria")
    else:
        r["dia_vencimento"] = ("OK", f"dia {dia}" + (f" · {regra}" if regra else ""))

    # 4. combustível e câmbio obrigatórios
    for campo in ("combustivel", "cambio"):
        v = card.get(campo, "")
        r[campo] = ("NAO_VERIFICAVEL", "vazio (obrigatório)") if v in ("", "Selecione") else ("OK", v)

    # 5. idade do veículo
    try:
        ano = int(card["ano_fabricacao"])
        idade = date.today().year - ano
        limite = 30 if tipo(card) == "caminhao" else 20
        r["idade"] = (("ALERTA", f"{idade} anos: só com autorização da diretoria (Flat)") if idade > limite else ("OK", f"{idade} anos"))
    except ValueError:
        r["idade"] = ("NAO_VERIFICAVEL", "ano de fabricação não lido")

    # 6. rastreador obrigatório
    motivos = []
    cc = cilindrada(card) if tipo(card) == "moto" else None
    if tipo(card) == "moto":
        if cc and cc > 501:
            motivos.append(f"moto de {cc}cc (> 501cc)")
        if uf == "PE" and cidade not in PE_ISENTAS:
            motivos.append("moto em Pernambuco (cidade não isenta)")
        if "RIBEIRÃO PRETO" in regional_cidade or "SP2 FORA" in regional_cidade:
            motivos.append(f"moto na regional {regional_cidade.split(' –')[0]}")
    elif valor and valor > 150_000:
        motivos.append(f"valor acima de R$150 mil")
    if tipo(card) == "leve" and "SP2 FORA" in regional_cidade and valor and valor >= 75_000:
        motivos.append("carro ≥ R$75 mil no SP2 FORA")
    rastreador = reais(card.get("_rastreador_no_card"))
    if "BYD" in card["veiculo"].upper():
        r["rastreador"] = ("ALERTA", "BYD não leva rastreador: conferir se está como 'adequação rastreador'")
    elif motivos:
        r["rastreador"] = (("OK" if rastreador else "DIVERGENTE"),
                           f"obrigatório ({'; '.join(motivos)}) · no card: {card.get('_rastreador_no_card') or '?'}")
    else:
        r["rastreador"] = ("OK", "não obrigatório")

    # 7. fotos da vistoria
    if pasta_fotos and (pasta_fotos / "fotos.json").exists():
        fotos = json.loads((pasta_fotos / "fotos.json").read_text(encoding="utf-8"))
        rotulos = [f["rotulo"] for f in fotos]
        faltando = [o for o in FOTOS_OBRIGATORIAS if not any(rt.startswith(o) for rt in rotulos)]
        r["fotos_obrigatorias"] = (("DIVERGENTE", "faltando: " + ", ".join(faltando)) if faltando else ("OK", f"{len(fotos)} fotos"))
        if ler_fotos:
            r["fotos_documento"] = _ler_documentos(pasta_fotos, fotos)
    elif pasta_fotos is not None:  # card real sem fotos baixadas (nos cards simulados não há fotos)
        r["fotos_obrigatorias"] = ("NAO_VERIFICAVEL", "fotos não baixadas")
    return r


def _ler_documentos(pasta_fotos, fotos):
    """OCR local rápido: foto de documento com quase nenhum texto provavelmente não mostra o documento."""
    import cv2
    from rapidocr_onnxruntime import RapidOCR

    from .ocr_teste import ler_imagem

    ocr = RapidOCR()
    suspeitas = []
    for f in fotos:
        if not f["rotulo"].startswith(FOTOS_DOCUMENTO):
            continue
        img = ler_imagem(pasta_fotos / f["arquivo"])
        if img is None:
            continue
        linhas = max(len(ocr(g)[0] or []) for g in (img, cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)))
        if linhas < 5:
            suspeitas.append(f"{f['rotulo']} ({linhas} linhas de texto)")
    if suspeitas:
        return ("ALERTA", "pouco ou nenhum texto, pode estar sem documento ou ilegível: " + "; ".join(suspeitas))
    return ("OK", "documentos com texto legível")
