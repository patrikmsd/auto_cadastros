"""Comando único: lê os cards livres da semana, decide a tabela (IA), confere as regras (código) e gera o relatório.

Uso:  python -m robo.conferir                      (até 3 cards livres da semana)
      python -m robo.conferir --max 10
      python -m robo.conferir --placa ISW5C08 --placa PGR-3236
      python -m robo.conferir --sem-ia             (só as regras por código)
Só leitura: a trava bloqueia qualquer gravação no PowerCRM.
"""
import argparse
import json
from datetime import datetime

from playwright.sync_api import sync_playwright

from . import config, decidir_tabela, regras
from .explorar import gravar_rede, ler_card, listar_cards, nova_saida, salvar
from .sessao import BLOQUEADOS, abrir, proteger

ICONE = {"OK": "✅", "DIVERGENTE": "❌", "ALERTA": "⚠️", "NAO_VERIFICAVEL": "❔"}
NOMES = {"tabela": "Tabela do CRM", "cota": "Cota de participação", "regional_autorizada": "Local de circulação",
         "valor_protegido": "Valor protegido × FIPE", "dia_vencimento": "Dia de vencimento", "combustivel": "Combustível",
         "cambio": "Câmbio", "idade": "Idade do veículo", "rastreador": "Rastreador", "fotos_obrigatorias": "Fotos obrigatórias",
         "fotos_documento": "Fotos de documento (OCR local)", "mensalidade": "Mensalidade × tabela de preço", "proprietario": "Associado × proprietário",
         "remarcado": "Chassi/motor remarcado", "documentos_ia": "Documentos × card (IA)",
         "fotos_vistoria": "Fotos da vistoria (IA)",
         "fipe": "Modelo × FIPE oficial (mês da cotação)"}


def brl(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def itens_da_ia(decisao, conf):
    if decisao is None:
        return {}
    itens = {}
    detalhe = f"esperada {conf['tabela_esperada']} · no card {conf['tabela_no_card']} · {decisao['justificativa']}"
    if decisao["precisa_revisao_humana"]:
        itens["tabela"] = ("ALERTA", f"IA pede revisão: {decisao['motivo_revisao']} · {detalhe}")
    else:
        itens["tabela"] = ("OK" if conf["mesma_tabela"] else "DIVERGENTE", detalhe)
    if "cota_esperada" in conf:
        texto = f"esperada R$ {brl(conf['cota_esperada'])} · no card R$ {brl(conf['cota_no_card'])}"
        itens["cota"] = ({True: "OK", False: "DIVERGENTE", None: "NAO_VERIFICAVEL"}[conf["cota_confere"]],
                         texto + (f" · {conf['cota_obs']}" if conf.get("cota_obs") else ""))
    return itens


def situacao(itens):
    status = [s for s, _ in itens.values()]
    return "DIVERGENTE" if "DIVERGENTE" in status else "VERIFICAR" if {"ALERTA", "NAO_VERIFICAVEL"} & set(status) else "OK"


def escrever_relatorio(saida, resultados):
    linhas = [f"# Conferência de cadastro – {datetime.now():%d/%m/%Y %H:%M}", "",
              f"Filtro: semana de {config.segunda_feira():%d/%m/%Y} · cards sem nome de analista · só leitura "
              f"(gravações bloqueadas: {len(BLOQUEADOS)})", "",
              "| Placa | Veículo | Cidade | Situação |", "|---|---|---|---|"]
    for r in resultados:
        c = r["card"]
        linhas.append(f"| {c['placa']} | {c['veiculo'][:45]} | {c['cidade_circulacao']}/{c['estado_circulacao']} | **{r['situacao']}** |")
    for r in resultados:
        c = r["card"]
        linhas += ["", f"## {c['placa']} – {c['veiculo']}", "", f"**Situação: {r['situacao']}**"
                   + (f" · IA: {r['modelo']}" if r.get("modelo") else "") + (f" · erro da IA: {r['erro_ia']}" if r.get("erro_ia") else ""),
                   "", "| | Item | Detalhe |", "|---|---|---|"]
        for chave, (status, detalhe) in r["itens"].items():
            linhas.append(f"| {ICONE[status]} | {NOMES.get(chave, chave)} | {str(detalhe).replace('|', '/')} |")
    (saida / "relatorio.md").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    salvar(saida, "relatorio.json", resultados)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--placa", action="append", help="card específico (pode repetir)")
    ap.add_argument("--max", type=int, default=3, help="quantos cards livres conferir (padrão 3)")
    ap.add_argument("--provedor", choices=["gemini", "claude"], default=config.ENV.get("ia_provedor", "gemini"))
    ap.add_argument("--sem-ia", action="store_true", help="não chama a IA (só regras por código)")
    ap.add_argument("--sem-ocr", action="store_true", help="não roda o OCR local nas fotos de documento")
    ap.add_argument("--login-automatico", action="store_true")
    ap.add_argument("--sem-tags", action="store_true",
                    help="teste: procura o card na semana sem exigir tag ANALISAR / status da vistoria (use com --placa)")
    args = ap.parse_args()
    if args.sem_tags:
        config.FILTRAR_TAGS = False
    saida = nova_saida()

    # 1) leitura: navegador aberto só o tempo de ler os cards
    lidos = []
    with sync_playwright() as p:
        browser, ctx, page = abrir(p, login_automatico=args.login_automatico)
        proteger(ctx)
        rede = gravar_rede(ctx)
        cards = listar_cards(page, saida)
        livres = [c for c in cards if not c["analistas"]]
        if args.placa:
            pedidas = {x.replace("-", "").upper() for x in args.placa}
            alvos = [c for c in livres if c.get("placa", "").replace("-", "") in pedidas]
            for c in cards:
                if c.get("placa", "").replace("-", "") in pedidas and c["analistas"]:
                    print(f"- {c['placa']}: tem analista ({', '.join(c['analistas'])}), não será aberto")
        else:
            alvos = livres[: args.max]
        print(f"{len(cards)} cards na semana · {len(livres)} livres · conferindo {len(alvos)}")
        for alvo in alvos:
            print(f"- lendo {alvo['placa']} ({alvo.get('veiculo', '')[:40]})")
            try:
                pasta_fotos, n = ler_card(page, ctx, alvo, saida / alvo["placa"], rede)
                lidos.append((saida / alvo["placa"], pasta_fotos))
            except Exception as e:
                print(f"  falhou ao ler: {e}")
        browser.close()

    # 2) conferência: IA decide a tabela, código confere o resto
    regras_texto = None if args.sem_ia else decidir_tabela.montar_regras()
    resultados = []
    for pasta, pasta_fotos in lidos:
        card = decidir_tabela.dados_do_card(pasta)
        print(f"- conferindo {card['placa']}")
        decisao, conf, erro = None, None, None
        if not args.sem_ia:
            try:
                decisao = decidir_tabela.perguntar_ao_modelo(regras_texto, card, args.provedor)
                conf = decidir_tabela.conferir(decisao, card)
            except (Exception, SystemExit) as e:
                erro = str(e)[:300]
                print(f"  IA falhou: {erro}")
        itens = {**itens_da_ia(decisao, conf), **regras.verificar(card, pasta_fotos, ler_fotos=not args.sem_ocr)}
        if erro:
            itens["tabela"] = ("NAO_VERIFICAVEL", f"IA indisponível: {erro}")
        modelo = (config.ENV.get("gemini_modelo_usado") if args.provedor == "gemini" else decidir_tabela.MODELO) if decisao else None
        resultados.append({"card": {k: v for k, v in card.items()}, "situacao": situacao(itens), "itens": itens,
                           "decisao_ia": decisao, "conferencia_tabela": conf, "modelo": modelo, "erro_ia": erro})
        print(f"  → {resultados[-1]['situacao']}")

    escrever_relatorio(saida, resultados)
    print(f"\nRelatório: {saida / 'relatorio.md'}")
    print(f"Gravações bloqueadas pela trava: {len(BLOQUEADOS)}")


if __name__ == "__main__":
    main()
