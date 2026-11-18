"""Conferência em lote (SÓ LEITURA): lista os cards do funil com o filtro das analistas e confere todos, sem preencher nada.

1. abre o funil com o filtro do sistema (criados desde config.FILTRO_DESDE, tags PENDENCIAS LEVES/CADASTRO COMPLETO + vistoria Em aprovação/Retornada) e pega a lista pelo próprio JSON
   do CRM (fetchNegotiations, blocos de 20: rola a coluna até vir o total do countNegotiations)
2. card com tag de analista (ex.: PAMMELA) ou sem placa → não abre, fica na lista como "pulado"
3. cada card livre: lê pelo código (como o demo), OCR local, fotos (gpt-6-luna), FIPE oficial (luna), tabela (IA),
   documentos (IA) e regras por código
4. grava depois de CADA card: lote.csv, lote.md e lote.json (todos os cards, inclusive os pulados) + conferencia.json
   na pasta de cada card. Parou no meio? --retomar <pasta> continua de onde parou.

Uso:  python -m robo.lote                         (desde 01/01/2020, todos os cards livres)
      python -m robo.lote --so-listar             (só a lista, sem abrir card)
      python -m robo.lote --max 5                 (os 5 primeiros livres)
      python -m robo.lote --inicio 2026-10-26 --fim 2026-10-30
      python -m robo.lote --codigo nEY5Y1A8Ed     (só esses, desde que estejam no filtro; pode repetir)
      python -m robo.lote --sem-fotos-ia          (sem a análise das fotos pela IA: mais rápido e barato)
      python -m robo.lote --retomar robo/saida/lote-20261030-101500
      python -m robo.lote --sem-painel            (sem o painel no navegador)
Painel (http://127.0.0.1:8765): lista do lote ao vivo, o card em conferência nas seções de sempre e "ver" em cada
card já conferido. Rever depois: python -m robo.painel robo/saida/lote-<data>
O demo (robo.demo) continua igual: este script só reaproveita as peças dele.
"""
import argparse
import csv
import json
import re
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

if __package__ in (None, ""):  # rodando como arquivo (py lote.py): vira pacote para os imports relativos
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "robo"

from playwright.sync_api import sync_playwright

from . import analisar_fotos, config, custo_ia, decidir_tabela, fipe, gpt, incertezas, painel, regras
from .conferir import ICONE, NOMES, escrever_relatorio, itens_da_ia, situacao
from .demo import ocr_documentos
from .explorar import gravar_rede, ler_card, salvar
from .sessao import BLOQUEADOS, GRAVADOS, abrir, esperar, proteger

# rola até o fim tudo o que tem barra de rolagem (a coluna do funil carrega o próximo bloco de 20 ao chegar no fim)

JS_ROLAR = r"""() => { let n = 0; document.querySelectorAll('*').forEach(e => {
  const s = getComputedStyle(e).overflowY;
  if (e.scrollHeight > e.clientHeight + 20 && /(auto|scroll)/.test(s)) { e.scrollTop = e.scrollHeight; n++; } });
  window.scrollTo(0, document.body.scrollHeight); return n; }"""
PLANO_CARD = [["3", "Ler o card e baixar fotos"], ["4", "OCR local dos documentos"], ["4b", "IA olha as fotos"], ["4c", "FIPE oficial"],
              ["5", "IA decide a tabela"], ["5b", "IA compara documentos"], ["6", "Conferência"], ["7", "Resultado"]]
COLUNAS = ["codigo", "placa", "veiculo", "criado", "vistoria", "vendedor", "analista", "acao", "situacao", "divergentes",
           "alertas", "fipe", "fotos_a_refazer", "avarias", "incertezas", "custo_rs", "segundos", "pasta"]


def _data(texto):
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"data inválida: {texto} (use 2026-10-26 ou 26/10/2026)")


def listar(page, inicio, fim):
    """Lista do funil com o filtro das analistas, lida do JSON que o CRM recebe. Devolve (itens, total, url)."""
    itens, total = {}, {"n": None}

    def ao_responder(resp):
        try:
            if "/internal/pipeline/fetchNegotiations" in resp.url:
                for it in resp.json().get("itens") or []:
                    itens[it["code"]] = it
            elif "/internal/pipeline/countNegotiations" in resp.url:
                total["n"] = sum(c.get("total", 0) for c in resp.json())
        except Exception:
            pass

    page.on("response", ao_responder)
    url = config.url_filtro_semana(inicio, fim)
    esperar(page, lambda: page.goto(url, wait_until="domcontentloaded"), maximo=12_000,
            pronto=lambda: total["n"] is not None and (len(itens) > 0 or total["n"] == 0))
    sem_novidade = 0
    while total["n"] is None or len(itens) < total["n"]:
        antes = len(itens)
        esperar(page, lambda: page.evaluate(JS_ROLAR), maximo=3500)
        sem_novidade = sem_novidade + 1 if len(itens) == antes else 0
        if sem_novidade >= 3:
            break
    page.remove_listener("response", ao_responder)
    return list(itens.values()), total["n"], url


def resumo(it):
    tags = [t.get("name", "") for t in it.get("tags") or []]
    placas = it.get("vehiclePlates") or []
    return {"codigo": it["code"], "id": it.get("id"), "placa": (placas[0] if placas else "").upper(),
            "veiculo": it.get("clientCarLine") or "", "criado": it.get("createdDate") or "",
            "vistoria": it.get("inspectionColumnName") or "", "coluna": (it.get("pipelineColumnName") or "").strip(),
            "vendedor": it.get("salesmanName") or "", "tags": tags,
            "analistas": [t for t in tags if t.strip().upper() in config.NOMES_ANALISTAS]}


def _itens_ocr(ocr, card):
    """Mesma conferência do demo (etapa 6) para o que o OCR local leu."""
    itens = {}
    do_card = {r for r, d in ocr.items() if d["crlv"] and "OK" in d["conferencia"].values()}
    corretos = [r for r in do_card if r.startswith("Anexo")]
    divergentes = [f"{r}: {campo} {c}" for r, d in ocr.items() for campo, c in d["conferencia"].items() if c != "OK"]
    if divergentes and corretos:
        divergentes.append(f"o CRLV certo está em '{corretos[0]}': vincular na vistoria")
    sem_texto = [r for r, d in ocr.items() if d["linhas"] < 5 and not r.startswith(("Selfie", "Número do chassi"))]
    itens["fotos_documento"] = (("DIVERGENTE", "documento × card: " + "; ".join(divergentes)) if divergentes else
                                ("ALERTA", "pouco ou nenhum texto: " + "; ".join(sem_texto)) if sem_texto else
                                ("OK", "documentos com texto legível") if ocr else
                                ("ALERTA", "nenhuma foto de documento na vistoria"))
    cpf_cnh = {c for d in ocr.values() if d["cnh"] for c in d["achados"].get("cpf", [])}
    cpf_crlv = {c for r in do_card for c in ocr[r]["achados"].get("cpf", [])}
    if cpf_cnh and cpf_crlv:
        itens["proprietario"] = (("OK", "CPF da CNH = CPF do proprietário no CRLV") if cpf_cnh & cpf_crlv else
                                 ("ALERTA", f"CPF da CNH ({', '.join(cpf_cnh)}) ≠ proprietário no CRLV ({', '.join(cpf_crlv)})"))
    remarcados = sorted({x for r in do_card for x in ocr[r]["achados"].get("remarcado", [])})
    if remarcados and card.get("_depreciacao") in ("", "Selecione", None):
        itens["remarcado"] = ("DIVERGENTE", f"documento diz {', '.join(r + ' REMARCADO' for r in remarcados)}, "
                                            "mas o card não tem depreciação de 30%")
    return itens


def conferir_card(pasta_card, pasta_fotos, regras_texto, com_fotos_ia=True):
    """Todas as conferências do demo, sem preencher nada. Devolve o resultado (o mesmo formato do relatorio.json)."""
    card = decidir_tabela.dados_do_card(pasta_card)
    placa = card["placa"]
    painel.definir("card", card)
    painel.fotos(pasta_fotos, json.loads((pasta_fotos / "fotos.json").read_text(encoding="utf-8")))
    painel.etapa("4", "OCR local dos documentos (nada sai do computador)")
    print("   🔎 OCR local dos documentos")
    ocr = ocr_documentos(pasta_card, pasta_fotos)

    vistoria = respostas = None
    if com_fotos_ia:
        print(f"   📸 IA olha as fotos ({gpt.MODELO})")
        painel.etapa("4b", f"IA analisa as fotos do veículo ({gpt.MODELO})")
        painel.definir("vistoria", {"fotos": {}, "resumo": None, "modelo": gpt.MODELO})
        t = time.time()
        # todas: documento também (às vezes vem no espaço errado e a IA precisa dizer o que a foto é)
        respostas, _ = analisar_fotos.analisar(pasta_fotos, todas=True, ao_terminar=painel.vistoria_foto)
        vistoria = analisar_fotos.resumir(respostas, card)
        painel.definir("vistoria", {"fotos": respostas, "resumo": vistoria, "modelo": gpt.MODELO, "segundos": round(time.time() - t, 1)})
        salvar(pasta_card, "analise_fotos.json", {"resumo": vistoria, "fotos": respostas})
        for x in vistoria["fora_do_lugar"]:
            incertezas.anotar(placa, f"Foto fora do lugar · {x['foto']}", x["e"], "IA viu na foto: confirmar olhando", f"foto '{x['foto']}'")
        for d in vistoria["vidros_danos"]:
            incertezas.anotar(placa, f"Vidro · {d['vidro']}", d["tipo"], "IA viu na foto: confirmar olhando", f"foto '{d['foto']}'")
        for a in vistoria["avarias"]:
            if a.get("gravidade") == "leve":  # desgaste leve (carro/caminhão antigo) fica só na análise das fotos
                continue
            incertezas.anotar(placa, f"Avaria · {a['local']}", f"{a['tipo']} ({a.get('gravidade', '')})",
                              "IA viu na foto: confirmar olhando (pode ser reflexo)", f"foto '{a['foto']}'")

    print("   🏷️  FIPE oficial (mês da cotação) + luna")
    painel.etapa("4c", f"FIPE oficial + {fipe.MODELO}: o modelo do card bate com o carro?")
    try:
        conf_fipe = fipe.conferir(pasta_card, card, ocr, respostas, passo=lambda t, f="": painel.passo("fipe", t, f))
    except Exception as e:
        painel.passo_fim("fipe", "erro", str(e)[:120])
        conf_fipe = {"status": "NAO_VERIFICAVEL", "detalhe": f"OBRIGATÓRIA e não feita (site da FIPE indisponível: {str(e)[:150]}): "
                                                             "conferir o modelo na FIPE manualmente"}
    painel.passo_fim("fipe", "erro" if conf_fipe["status"] == "NAO_VERIFICAVEL" else "ok", conf_fipe["detalhe"][:90])
    painel.definir("fipe", conf_fipe)
    salvar(pasta_card, "fipe.json", conf_fipe)
    if conf_fipe["status"] != "OK":
        incertezas.anotar(placa, "Modelo FIPE", card["veiculo"], conf_fipe["detalhe"], f"FIPE oficial {conf_fipe.get('mes', '')}")

    print("   📋 IA decide a tabela do CRM")
    painel.etapa("5", "IA decide a tabela do CRM")
    decisao = conf = erro = None
    t = time.time()
    try:
        decisao = decidir_tabela.perguntar_ao_modelo(regras_texto, card, "gpt")
        conf = decidir_tabela.conferir(decisao, card)
        painel.definir("ia_tabela", {"decisao": decisao, "conferencia": conf, "modelo": config.ENV.get("gpt_modelo_usado"),
                                     "segundos": round(time.time() - t, 1)})
    except (Exception, SystemExit) as e:
        erro = str(e)[:300]
        painel.definir("ia_tabela", {"decisao": None, "erro": erro})

    analise_docs = None
    docs = {r: {"linhas": d["linhas_veiculo"], "placa_lida": d["achados"].get("placa", []), "chassi_lido": d["achados"].get("chassi", [])}
            for r, d in ocr.items() if d["crlv"] or r.startswith("Número do chassi")}
    if docs:
        print("   📄 IA compara os documentos com o card")
        painel.etapa("5b", "IA compara os documentos com o card")
        try:
            analise_docs = decidir_tabela.analisar_documentos(card, docs, "gpt")
            painel.definir("ia_docs", analise_docs)
        except (Exception, SystemExit) as e:
            print(f"      IA falhou: {str(e)[:200]}")

    painel.etapa("6", "Conferência por código (tabela, cota e regras)")
    itens = {**itens_da_ia(decisao, conf), **regras.verificar(card, pasta_fotos, ler_fotos=False), **_itens_ocr(ocr, card)}
    if erro:
        itens["tabela"] = ("NAO_VERIFICAVEL", f"IA indisponível: {erro}")
    if analise_docs:
        errados = [d["origem"] for d in analise_docs["documentos"] if d["corresponde_ao_card"] == "nao"]
        itens["documentos_ia"] = (("DIVERGENTE", f"de outro veículo: {'; '.join(errados)} · {analise_docs['conclusao']}") if errados else
                                  ("ALERTA", analise_docs["conclusao"]) if analise_docs["precisa_revisao_humana"] or
                                  analise_docs["observacoes_graves"] else ("OK", analise_docs["conclusao"]))
    itens["fipe"] = (conf_fipe["status"], conf_fipe["detalhe"])
    if vistoria:
        itens["fotos_vistoria"] = analisar_fotos.conferencia(vistoria)
    painel.definir("conferencia", [{"nome": NOMES.get(k, k), "status": st, "detalhe": d} for k, (st, d) in itens.items()])
    painel.etapa("7", "Resultado")
    painel.definir("situacao", situacao(itens))
    return {"card": card, "situacao": situacao(itens), "itens": itens, "decisao_ia": decisao, "conferencia_tabela": conf,
            "modelo": config.ENV.get("gpt_modelo_usado") if decisao else None, "erro_ia": erro, "ocr": ocr,
            "analise_documentos_ia": analise_docs, "vistoria_ia": vistoria, "fipe": conf_fipe}


def _linha_lista(c, r=None, custo=None, segundos=None, pasta=None, acao=None):
    linha = {"codigo": c["codigo"], "placa": c["placa"], "veiculo": c["veiculo"], "criado": c["criado"], "vistoria": c["vistoria"],
             "vendedor": c["vendedor"], "analista": ", ".join(c["analistas"]), "acao": acao or "", "situacao": "", "divergentes": "",
             "alertas": "", "fipe": "", "fotos_a_refazer": "", "avarias": "", "incertezas": "", "custo_rs": "", "segundos": "",
             "pasta": str(pasta or "")}
    if r:
        itens = r["itens"]
        linha.update(situacao=r["situacao"],
                     divergentes="; ".join(NOMES.get(k, k) for k, (s, _) in itens.items() if s == "DIVERGENTE"),
                     alertas="; ".join(NOMES.get(k, k) for k, (s, _) in itens.items() if s in ("ALERTA", "NAO_VERIFICAVEL")),
                     fipe=("confirmado" if r["fipe"].get("certeza") else r["fipe"]["status"].lower()),
                     fotos_a_refazer=", ".join(p["foto"] for p in (r["vistoria_ia"] or {}).get("fotos_com_problema", [])),
                     avarias=len((r["vistoria_ia"] or {}).get("avarias", [])) if r["vistoria_ia"] else "",
                     incertezas=sum(1 for x in incertezas.ANOTADAS if x["card"] == r["card"]["placa"]),
                     custo_rs=f"{custo * custo_ia.DOLAR:.4f}".replace(".", ",") if custo is not None else "",
                     segundos=round(segundos) if segundos else "")
    return linha


def _estado(l):
    a = l["acao"]
    return ("conferido" if l["situacao"] else "conferindo" if a == "conferindo" else "fila" if a == "na fila" else
            "erro" if a.startswith("erro") else "pulado")


def painel_lote(linhas, filtro, atual=None):
    painel.definir("lote", {"filtro": filtro["descricao"], "atual": atual, "cards": [
        {**l, "estado": _estado(l), "painel": str(Path(l["pasta"]) / "painel_card.json") if l["pasta"] and (Path(l["pasta"]) / "painel_card.json").exists() else ""}
        for l in linhas]})


def gravar_lista(saida, linhas, filtro):
    painel_lote(linhas, filtro)
    with (saida / "lote.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS, delimiter=";")
        w.writeheader()
        w.writerows(linhas)
    salvar(saida, "lote.json", {"filtro": filtro, "cards": linhas})
    icone = {"OK": "✅", "VERIFICAR": "⚠️", "DIVERGENTE": "❌"}
    conferidos = [l for l in linhas if l["situacao"]]
    soma = sum(float(l["custo_rs"].replace(",", ".")) for l in conferidos if l["custo_rs"])
    md = [f"# Conferência em lote – {datetime.now():%d/%m/%Y %H:%M}", "",
          f"Filtro: {filtro['descricao']} · só leitura, nada preenchido (gravações bloqueadas: {len(BLOQUEADOS)}, gravadas: {len(GRAVADOS)})", "",
          f"**{len(linhas)} cards no filtro · {len(conferidos)} conferidos** · "
          + " · ".join(f"{icone[s]} {sum(1 for l in conferidos if l['situacao'] == s)} {s.lower()}" for s in icone)
          + f" · custo de IA R$ {soma:.2f}".replace(".", ","), "",
          "| Placa | Veículo | Vistoria | Situação | Divergências | Alertas | FIPE | Fotos a refazer |", "|---|---|---|---|---|---|---|---|"]
    for l in linhas:
        sit = f"{icone.get(l['situacao'], '')} **{l['situacao']}**" if l["situacao"] else f"⏭️ {l['acao']}"
        md.append(f"| {l['placa'] or '—'} | {l['veiculo'][:40]} | {l['vistoria']} | {sit} | {l['divergentes'] or '—'} | "
                  f"{l['alertas'] or '—'} | {l['fipe'] or '—'} | {l['fotos_a_refazer'] or '—'} |")
    md += ["", "Detalhe de cada card: relatorio.md · pasta de cada card: conferencia.json, fipe.json, analise_fotos.json"]
    (saida / "lote.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Confere vários cards do funil (só leitura, sem preencher nada).")
    ap.add_argument("--inicio", type=_data, help="data de criação inicial (padrão: filtro_desde do .env, 01/01/2020)")
    ap.add_argument("--fim", type=_data, help="data de criação final (padrão: hoje)")
    ap.add_argument("--max", type=int, default=0, help="no máximo N cards conferidos (padrão: todos os livres)")
    ap.add_argument("--codigo", action="append", help="só estes códigos de card (precisam estar no filtro; pode repetir)")
    ap.add_argument("--so-listar", action="store_true", help="só monta a lista, não abre nenhum card")
    ap.add_argument("--sem-fotos-ia", action="store_true", help="não manda as fotos para a IA")
    ap.add_argument("--retomar", help="pasta de um lote anterior: pula os cards já conferidos")
    ap.add_argument("--sem-painel", action="store_true", help="não abre o painel no navegador")
    args = ap.parse_args()
    inicio = args.inicio or config.FILTRO_DESDE
    fim = (args.fim or date.today()) + timedelta(days=1)  # o CRM usa o fim como exclusivo
    config.FILTRAR_TAGS = True  # o lote sempre usa o filtro das analistas

    saida = Path(args.retomar) if args.retomar else config.PASTA_SAIDA / f"lote-{datetime.now():%Y%m%d-%H%M%S}"
    saida.mkdir(parents=True, exist_ok=True)
    filtro = {"inicio": f"{inicio:%d/%m/%Y}", "fim": f"{fim - timedelta(days=1):%d/%m/%Y}", "tags": config.TAGS_ANALISAR,
              "vistoria": config.VISTORIA, "descricao": f"criados de {inicio:%d/%m/%Y} a {fim - timedelta(days=1):%d/%m/%Y} · "
                                                        "tags ANALISAR (pendências leves, cadastro completo) · vistoria Em aprovação/Retornada"}
    if not args.sem_painel:
        painel.iniciar()
        painel.definir("pasta", str(saida))
        painel.definir("plano", [["1", "Abrir o PowerCRM"], ["2", "Listar os cards do filtro"]])
        painel.etapa("1", "Abrindo o PowerCRM")
    print(f"CONFERÊNCIA EM LOTE · {config.BASE_URL} · SÓ LEITURA · nada é preenchido\n   {filtro['descricao']}\n   saída: {saida}")
    inicio_lote = time.time()

    with sync_playwright() as p:
        browser, ctx, page = abrir(p, login_automatico=True)
        proteger(ctx)
        rede = gravar_rede(ctx)
        painel.etapa("2", "Listando os cards do filtro das analistas")
        itens, total, url = listar(page, inicio, fim)
        filtro["url"] = url
        cards = [resumo(it) for it in itens]
        salvar(saida, "lista_crm.json", itens)
        print(f"\n📋 {len(cards)} cards no filtro" + (f" (o CRM conta {total})" if total is not None else "")
              + ("  ⚠️ não carregou todos" if total and len(cards) < total else ""))

        anteriores = {}
        if args.retomar and (saida / "lote.json").exists():
            anteriores = {l["codigo"]: l for l in json.loads((saida / "lote.json").read_text(encoding="utf-8"))["cards"] if l["situacao"]}
        pedidos = {c.strip() for c in args.codigo or []}
        linhas, fila = [], []
        for c in cards:
            if c["codigo"] in anteriores:
                linhas.append(anteriores[c["codigo"]])
            elif c["analistas"]:
                linhas.append(_linha_lista(c, acao=f"pulado: analista {', '.join(c['analistas'])}"))
            elif not c["placa"]:
                linhas.append(_linha_lista(c, acao="pulado: sem placa"))
            elif pedidos and c["codigo"] not in pedidos:
                linhas.append(_linha_lista(c, acao="fora da seleção (--codigo)"))
            elif args.max and len(fila) >= args.max:
                linhas.append(_linha_lista(c, acao="não conferido (--max)"))
            else:
                linhas.append(_linha_lista(c, acao="na fila"))
                fila.append((len(linhas) - 1, c))
        for c in cards:
            marca = "✔ já conferido" if c["codigo"] in anteriores else ("⏭️ analista " + ", ".join(c["analistas"])) if c["analistas"] else \
                "⏭️ sem placa" if not c["placa"] else "→ conferir" if any(c is x for _, x in fila) else "· fora"
            print(f"   {c['placa'] or '(sem placa)':<8} {c['veiculo'][:42]:<42} {c['vistoria']:<12} {marca}")
        for pedido in pedidos - {c["codigo"] for c in cards}:
            print(f"   ⚠️ {pedido}: não está no filtro (sem tag ANALISAR, vistoria fora de Em aprovação/Retornada ou fora das datas)")
        gravar_lista(saida, linhas, filtro)
        if args.so_listar or not fila:
            browser.close()
            print(f"\nLista em {saida / 'lote.csv'} · {saida / 'lote.md'}")
            painel.finalizar("concluido")
            return

        regras_texto = decidir_tabela.montar_regras()
        resultados = []
        for n, (i, c) in enumerate(fila, 1):
            print(f"\n{'═' * 78}\n {n}/{len(fila)} · {c['placa']} · {c['veiculo'][:50]} · {c['codigo']}\n{'═' * 78}", flush=True)
            pasta = saida / c["placa"]
            t, chamadas = time.time(), len(custo_ia.DESTA_EXECUCAO)
            linhas[i] = {**linhas[i], "acao": "conferindo"}
            painel.novo_card()
            painel.definir("plano", PLANO_CARD)
            painel.etapa("3", f"Card {n}/{len(fila)} · lendo {c['placa']} no CRM e baixando as fotos")
            painel_lote(linhas, filtro, i)
            try:
                pasta.mkdir(parents=True, exist_ok=True)
                salvar(pasta, "cards_filtro.json", [{"placa": c["placa"], "veiculo": c["veiculo"], "criado": c["criado"], "analistas": []}])
                alvo = {"placa": c["placa"], "veiculo": c["veiculo"], "criado": c["criado"], "analistas": c["analistas"]}
                print("   🌐 lendo o card no CRM (só leitura) e baixando as fotos")
                pasta_fotos, nfotos = ler_card(page, ctx, alvo, pasta, rede, busca=c["codigo"])
                print(f"      {nfotos} fotos")
                r = conferir_card(pasta, pasta_fotos, regras_texto, not args.sem_fotos_ia)
                salvar(pasta, "conferencia.json", r)
                custo = sum(d or 0 for _, _, d in custo_ia.DESTA_EXECUCAO[chamadas:])
                linhas[i] = _linha_lista(c, r, custo, time.time() - t, pasta, acao="conferido")
                painel.guardar_card(pasta / "painel_card.json", {"custo_card_usd": custo})
                resultados.append(r)
                print(f"\n   {ICONE.get({'VERIFICAR': 'ALERTA'}.get(r['situacao'], r['situacao']), '')} {r['situacao']}"
                      + "".join(f"\n      {ICONE[s]} {NOMES.get(k, k)}: {str(d)[:110]}" for k, (s, d) in r["itens"].items() if s != "OK")
                      + f"\n   ⏱️ {time.time() - t:.0f}s · R$ {custo * custo_ia.DOLAR:.4f}")
            except Exception as e:
                linhas[i] = _linha_lista(c, acao=f"erro: {str(e)[:120]}", pasta=pasta)
                (pasta / "erro.txt").write_text(traceback.format_exc(), encoding="utf-8")
                painel.guardar_card(pasta / "painel_card.json")
                print(f"   ❌ falhou: {e}")
            gravar_lista(saida, linhas, filtro)  # a cada card: nada se perde se parar no meio
            if resultados:
                escrever_relatorio(saida, resultados)
        salvar(saida, "gravacoes_bloqueadas.json", BLOQUEADOS)
        browser.close()

    if incertezas.ANOTADAS:
        incertezas.salvar(saida)
    soma = custo_ia.total() or 0
    feitos = [l for l in linhas if l["situacao"]]
    print(f"\n{'═' * 78}\n FIM · {len(feitos)} conferidos de {len(cards)} no filtro · {time.time() - inicio_lote:.0f}s · "
          f"IA R$ {soma * custo_ia.DOLAR:.2f} · gravações bloqueadas {len(BLOQUEADOS)} · gravadas {len(GRAVADOS)}\n{'═' * 78}")
    for l in linhas:
        print(f"   {l['placa'] or '(sem placa)':<8} {l['situacao'] or l['acao']:<12} {l['divergentes'][:60]}")
    print(f"\n   Lista: {saida / 'lote.csv'}\n   Resumo: {saida / 'lote.md'}\n   Detalhe: {saida / 'relatorio.md'}")
    if painel.ATIVO:
        painel.finalizar("concluido")
        try:
            input("\n   Painel continua aberto para conferir. Enter para encerrar.\n")
        except (EOFError, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    main()
