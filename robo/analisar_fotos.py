"""Fotos da vistoria → gpt-6-luna: tira o máximo de informação de cada foto e cruza com o card.

Uso: python -m robo.analisar_fotos [--pasta testes/amostras/PKD6E70] [--modelo gpt-6-luna] [--todas]
Padrão: só as fotos do veículo; CNH e comprovante ficam de fora (já lidos pelo OCR local).
--todas: manda também CNH e comprovante (a IA só diz o tipo e se está legível; não transcreve dados pessoais).
O que a IA aponta (avaria, vidro danificado, foto errada) é sugestão para a analista olhar, nunca reprovação.
"""
import argparse
import json
import re
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, custo_ia
from .gpt import perguntar_gpt

PESSOAIS = re.compile(r"documento cnh|comprovante", re.I)
DOC_ROTULO = re.compile(r"documento|cnh|crlv|comprovante|anexo", re.I)  # espaços feitos para documento (anexo: descrição livre)
DOC_TIPOS = {"cnh": "uma CNH", "crlv": "um CRLV (documento do veículo)", "comprovante": "um comprovante de endereço"}
PERGUNTA = """Você é vistoriador de uma associação de proteção veicular. Esta foto foi enviada no espaço "{rotulo}" da vistoria,
mas pode ter sido posta no espaço ERRADO: diga em "tipo_real" o que ela realmente mostra.
Extraia o máximo de informação útil sobre o VEÍCULO (não descreva pessoas). A foto pode estar girada.
Olhe com atenção os VIDROS (para-brisa, laterais, traseiro, retrovisores, faróis e lanternas): trinca, rachadura,
lascado (ponto de pedra), quebrado, película, adesivo que atrapalha a visão.
Se for documento, diga só o tipo, de qual veículo é e se está legível/íntegro; não transcreva nome, CPF, RG ou CNH.
Só afirme o que dá para ver; o que não der para ver deixe "" ou null. Nada de chute: em dúvida, certeza "baixa".
Responda só JSON:
{{"o_que_a_foto_mostra": "", "tipo_real": "veiculo|painel|chassi|chaves|cnh|crlv|comprovante|selfie|assinatura|outro",
 "corresponde_ao_rotulo": true, "motivo_se_nao_corresponde": "",
 "veiculo": {{"tipo": "carro|moto|pick-up|caminhao|van|onibus|outro|", "marca": "", "modelo": "", "versao_ou_detalhes": "",
             "carroceria": "sedã|hatch|SUV|picape|perua|furgão|", "cor": "", "placa_visivel": "", "ano_aparente": ""}},
 "painel": {{"aparece": false, "ligado": null, "km": "", "luzes_de_alerta_acesas": [], "nivel_combustivel": "",
             "mensagens_na_tela": [], "cambio_automatico": null}},
 "vidros": {{"aparecem": false, "para_brisa": "", "laterais": "", "traseiro": "", "retrovisores": "", "farois_lanternas": "",
            "danos": [{{"vidro": "", "tipo": "", "descricao": "", "certeza": "alta|media|baixa"}}]}},
 "partes_visiveis": [{{"parte": "", "estado": "bom|regular|ruim", "observacao": ""}}],
 "chassi_visivel": "", "motor": {{"aparece": false, "observacoes": ""}},
 "pneus": {{"aparece": false, "estado": "bom|regular|ruim|", "observacoes": ""}},
 "avarias": [{{"local": "", "tipo": "", "gravidade": "leve|media|grave", "certeza": "alta|media|baixa"}}],
 "estado_geral": "bom|regular|ruim|nao_da_para_avaliar", "estado_geral_descricao": "", "itens_observados": [],
 "qualidade_da_foto": {{"nitida": true, "iluminacao": "boa|regular|ruim", "problemas": []}},
 "pontos_de_atencao": [], "confianca": "alta|media|baixa"}}"""


def e_documento(rotulo, resposta=None):
    """Documento pelo espaço em que foi enviado OU pelo que a IA viu na foto (CNH posta no espaço das chaves, por exemplo)."""
    return bool(DOC_ROTULO.search(rotulo)) or (resposta or {}).get("tipo_real") in DOC_TIPOS


def fora_do_lugar(resultados):
    """Documento enviado num espaço que não é de documento: vira aviso para o analista."""
    return [{"foto": r, "e": DOC_TIPOS[v["tipo_real"]], "mostra": v.get("o_que_a_foto_mostra", "")}
            for r, v in resultados.items() if v.get("tipo_real") in DOC_TIPOS and not DOC_ROTULO.search(r)]


def fotos_para_analisar(pasta, todas=False):
    fotos = json.loads((pasta / "fotos.json").read_text(encoding="utf-8"))
    return fotos if todas else [f for f in fotos if not PESSOAIS.search(f["rotulo"])]


def analisar(pasta, modelo=None, todas=False, ao_terminar=lambda rotulo, resposta: None):
    """Uma chamada por foto, 8 por vez. ao_terminar(rotulo, resposta) é chamado a cada foto pronta (painel ao vivo)."""
    def uma(f):
        t = time.time()
        try:
            r = perguntar_gpt(f"vistoria · {f['rotulo']}", PERGUNTA.format(rotulo=f["rotulo"]), [pasta / f["arquivo"]],
                              modelo, esforco="low")
        except (Exception, SystemExit) as e:
            r = {"erro": str(e)[:300]}
        r["_segundos"] = round(time.time() - t, 1)
        ao_terminar(f["rotulo"], r)
        return f["rotulo"], r

    inicio = time.time()
    with ThreadPoolExecutor(8) as ex:
        resultados = dict(ex.map(uma, fotos_para_analisar(pasta, todas)))
    return resultados, time.time() - inicio


def _norm(t):
    return unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().upper().strip()


def _mais_comum(valores):
    valores = [v for v in valores if v]
    return Counter(valores).most_common(1)[0][0] if valores else ""


def resumir(resultados, card=None):
    """Junta as fotos numa visão só: veículo, painel, vidros, avarias, fotos com problema e cruzamento com o card."""
    card = card or {}
    ok = {r: v for r, v in resultados.items() if "erro" not in v}
    # documento (CRLV/CNH) pode ser de outro veículo: o veículo consolidado vem só das fotos do carro
    do_carro = {r: v for r, v in ok.items() if not e_documento(r, v)}
    confiaveis = [v.get("veiculo") or {} for v in do_carro.values() if v.get("confianca") != "baixa"]
    veiculo = {k: _mais_comum(_norm(x.get(k)) for x in confiaveis) for k in ("tipo", "marca", "modelo", "carroceria", "cor")}
    placas = Counter(re.sub(r"[^A-Z0-9]", "", _norm((v.get("veiculo") or {}).get("placa_visivel")))
                     for v in do_carro.values())
    placas.pop("", None)

    paineis = {r: v["painel"] for r, v in ok.items() if (v.get("painel") or {}).get("aparece")}
    painel = {"fotos": list(paineis),
              "ligado": True if any(p.get("ligado") for p in paineis.values()) else
              (False if any(p.get("ligado") is False for p in paineis.values()) else None),
              "km": sorted({p["km"] for p in paineis.values() if p.get("km")}),
              "luzes": sorted({l for p in paineis.values() for l in p.get("luzes_de_alerta_acesas") or []}),
              "mensagens": sorted({m for p in paineis.values() for m in p.get("mensagens_na_tela") or []}),
              "combustivel": _mais_comum(p.get("nivel_combustivel") for p in paineis.values()),
              "cambio_automatico": next((p["cambio_automatico"] for p in paineis.values()
                                         if p.get("cambio_automatico") is not None), None)}

    vidros = [{"foto": r, **d} for r, v in ok.items() for d in (v.get("vidros") or {}).get("danos") or [] if d.get("tipo")]
    avarias = [{"foto": r, **a} for r, v in ok.items() for a in v.get("avarias") or [] if a.get("tipo")]
    problemas = []
    for r, v in resultados.items():
        q = v.get("qualidade_da_foto") or {}
        motivo = ("IA falhou: " + v["erro"]) if "erro" in v else \
                 (v.get("motivo_se_nao_corresponde") or v.get("o_que_a_foto_mostra") or "não mostra o que o rótulo pede") \
                 if v.get("corresponde_ao_rotulo") is False and not re.search(r"anexo", r, re.I) else \
                 ("foto escura" if q.get("iluminacao") == "ruim" else "foto sem nitidez" if q.get("nitida") is False else "")
        if motivo:
            problemas.append({"foto": r, "motivo": motivo})
    estados = Counter(v.get("estado_geral") for v in do_carro.values() if v.get("estado_geral") not in (None, "", "nao_da_para_avaliar"))
    pneus = Counter((v.get("pneus") or {}).get("estado") for v in ok.values()
                    if (v.get("pneus") or {}).get("aparece") and (v.get("pneus") or {}).get("estado"))

    # cruzamento com o card (sugestão: a IA pode errar modelo/placa)
    cruzamento = []
    texto_card = _norm(card.get("veiculo"))
    if card.get("placa") and placas:
        lida = placas.most_common(1)[0][0]
        cruzamento.append(("placa", "OK" if lida == _norm(card["placa"]) else "ALERTA",
                           f"fotos: {', '.join(placas)} · card: {card['placa']}"))
    for k in ("marca", "modelo"):
        if veiculo[k] and texto_card:
            palavras = [p for p in re.split(r"\W+", veiculo[k]) if len(p) > 2] or [veiculo[k]]
            cruzamento.append((k, "OK" if any(p in texto_card for p in palavras) else "ALERTA",
                               f"fotos: {veiculo[k]} · card: {card['veiculo']}"))
    tipo_card = _norm(card.get("tipo_veiculo"))
    if veiculo["tipo"] and tipo_card:
        grupo = {"CARRO": "LEVE|AUTOMOVEL|CARRO|PASSEIO", "MOTO": "MOTO", "PICK-UP": "PICK|UTILIT|LEVE",
                 "CAMINHAO": "CAMINH|PESADO", "VAN": "VAN|UTILIT|LEVE", "ONIBUS": "ONIBUS|PESADO"}.get(veiculo["tipo"], veiculo["tipo"])
        cruzamento.append(("tipo", "OK" if re.search(grupo, tipo_card) else "ALERTA",
                           f"fotos: {veiculo['tipo']} · card: {card['tipo_veiculo']}"))
    if painel["cambio_automatico"] is not None and card.get("cambio"):
        auto_card = bool(re.search(r"AUT|CVT", _norm(card["cambio"])))
        cruzamento.append(("câmbio", "OK" if auto_card == painel["cambio_automatico"] else "ALERTA",
                           f"painel: {'automático' if painel['cambio_automatico'] else 'manual'} · card: {card['cambio']}"))
    return {"fotos_analisadas": len(resultados), "falhas": len(resultados) - len(ok), "veiculo": veiculo,
            "placas_lidas": dict(placas), "painel": painel, "vidros_danos": vidros, "avarias": avarias,
            "fotos_com_problema": problemas, "fora_do_lugar": fora_do_lugar(ok), "estado_geral": dict(estados), "pneus": dict(pneus),
            "cruzamento": [{"item": i, "status": s, "detalhe": d} for i, s, d in cruzamento]}


def conferencia(resumo):
    """Item para a conferência do demo: (status, detalhe). Nunca DIVERGENTE: é leitura de IA, vai para olho humano."""
    partes = [f"{c['item']} ≠ card ({c['detalhe']})" for c in resumo["cruzamento"] if c["status"] != "OK"]
    partes += [f"a foto do espaço '{x['foto']}' é {x['e']}" for x in resumo.get("fora_do_lugar") or []]
    if resumo["fotos_com_problema"]:
        partes.append(f"{len(resumo['fotos_com_problema'])} foto(s) a refazer/conferir: "
                      + ", ".join(p["foto"] for p in resumo["fotos_com_problema"]))
    if resumo["vidros_danos"]:
        partes.append("vidro: " + "; ".join(f"{d['vidro']} {d['tipo']}" for d in resumo["vidros_danos"]))
    if resumo["avarias"]:
        partes.append("possíveis avarias: " + "; ".join(f"{a['local']} ({a['tipo']}, {a.get('gravidade', '')})"
                                                       for a in resumo["avarias"]))
    v = resumo["veiculo"]
    base = " ".join(x for x in (v["marca"], v["modelo"], v["cor"]) if x) or "veículo não identificado"
    if resumo["painel"]["ligado"] is not None:
        base += f" · painel {'ligado' if resumo['painel']['ligado'] else 'desligado'}"
    if resumo["painel"]["km"]:
        base += f" · km {', '.join(resumo['painel']['km'])}"
    return ("ALERTA", base + " · " + " · ".join(partes)) if partes else ("OK", base + " · fotos conferem com o card")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pasta", default=str(config.PASTA_AMOSTRAS / "PKD6E70"))
    ap.add_argument("--modelo", default="gpt-6-luna")
    ap.add_argument("--todas", action="store_true", help="inclui CNH e comprovante")
    args = ap.parse_args()
    pasta = Path(args.pasta)
    resultados, segundos = analisar(pasta, args.modelo, args.todas)
    resumo = resumir(resultados)
    (config.PASTA_SAIDA / "analise_fotos.json").write_text(
        json.dumps({"resumo": resumo, "fotos": resultados}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    soma = custo_ia.total() or 0
    print(f"\n{len(resultados)} fotos · {segundos:.0f}s no total (8 por vez) · R$ {soma * custo_ia.DOLAR:.4f} "
          f"(US$ {soma:.4f}) · detalhes em {config.PASTA_SAIDA / 'analise_fotos.json'}")


if __name__ == "__main__":
    main()
