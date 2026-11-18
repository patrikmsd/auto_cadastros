"""Passo 2: cards falsos SIMULADOS (nada é criado no PowerCRM).

Cada caso monta um card com os mesmos campos que o explorar.py lê de um card real, com ou sem erro de propósito,
passa pela mesma IA (decidir_tabela) e pelas mesmas regras (regras.py) e compara com o resultado esperado.

Uso:  python -m robo.testar_casos                 (todos)
      python -m robo.testar_casos --caso 4 --caso 16
      python -m robo.testar_casos --sem-ia        (só regras por código)
"""
import argparse
import time
from datetime import datetime

from . import config, decidir_tabela, regras
from .conferir import itens_da_ia, situacao
from .explorar import salvar

CARRO, MOTO = "Carro ou utilitário pequeno", "Moto"

# (nº, descrição, tipo, veículo, ano fab., combustível, câmbio, uber, cidade, UF, valor, tabela colocada no card,
#  rastreador no card, tabela esperada (código), regional esperada, o card tem erro de propósito?)
CASOS = [
    (1, "Moto normal Fora", MOTO, "HONDA, CG 150 FAN ESDi 2011", 2010, "Flex", "Manual", False, "Caçapava", "São Paulo", 11390, "06 - MOTOCICLETAS (CC)", 0, "06", "FORA", False),
    (2, "Leve 4% Dentro", CARRO, "VW - VolksWagen, Gol 1.0 Flex 2015", 2015, "Flex", "Manual", False, "Goiânia", "Goiás", 32000, "01 - LEVES (4%)", 0, "01", "DENTRO", False),
    (3, "Leve 6% Fora (interior GO)", CARRO, "VW - VolksWagen, Virtus 1.6 MSI Flex 2020", 2020, "Flex", "Manual", False, "Rio Verde", "Goiás", 70000, "02 - LEVES (7%)", 0, "02", "FORA", False),
    (4, "Pick-up diesel na tabela flex", CARRO, "Toyota, Hilux CD SRV 4x4 2.8 TDI Diesel Aut. 2018", 2018, "Diesel", "Automático", False, "Rio Verde", "Goiás", 180000, "04 - PICKUPS FLEX (7%)", 35, "05", "FORA", True),
    (6, "Uber na tabela normal", CARRO, "GM - Chevrolet, Onix 1.0 Flex 2021", 2021, "Flex", "Manual", True, "Goiânia", "Goiás", 60000, "01 - LEVES (4%)", 0, "03", "DENTRO", True),
    (7, "Commander diesel", CARRO, "Jeep, Commander Overland 2.0 TD Diesel Aut. 2023", 2023, "Diesel", "Automático", False, "Goiânia", "Goiás", 200000, "15 - JEEP COMMANDER DIESEL (7%)", 35, "15", "DENTRO", False),
    (8, "Fusion na tabela leves", CARRO, "Ford, Fusion SEL 2.0 Aut. 2016", 2016, "Gasolina", "Automático", False, "Rio Verde", "Goiás", 70000, "02 - LEVES (7%)", 0, "13", "FORA", True),
    (9, "Híbrido", CARRO, "Toyota, Corolla Cross XRX Hybrid 1.8 Aut. 2022", 2022, "Gasolina/Elétrico", "Automático", False, "Goiânia", "Goiás", 150000, "04 - PICK-UPS (7%) (HÍBRIDO)", 0, "04", "DENTRO", False),
    (10, "22 anos sem autorização", CARRO, "VW - VolksWagen, Gol 1.0 Flex 2004", 2004, "Flex", "Manual", False, "Goiânia", "Goiás", 15000, "01 - LEVES (4%)", 0, "99", "DENTRO", True),
    (12, "BYD", CARRO, "BYD, Dolphin EV (Elétrico) 2024", 2024, "Elétrico", "Automático", False, "Goiânia", "Goiás", 120000, "02 - LEVES (6%)", 0, "02", "DENTRO", False),
    (13, "Câmbio vazio", CARRO, "VW - VolksWagen, Gol 1.0 Flex 2015", 2015, "Flex", "Selecione", False, "Goiânia", "Goiás", 32000, "01 - LEVES (4%)", 0, "01", "DENTRO", True),
    (14, "Moto 750cc sem rastreador", MOTO, "HONDA, NC 750X 2022", 2022, "Gasolina", "Manual", False, "Rio Verde", "Goiás", 50000, "06 - MOTOCICLETAS (CC)", 0, "06", "FORA", True),
    (15, "Moto especial", MOTO, "BMW, G 310 GS 2023", 2023, "Gasolina", "Manual", False, "Goiânia", "Goiás", 35000, "23 - ESPECIAL MOTO (15%)", 35, "23", "DENTRO", False),
    (16, "Geely EX5 na Flat", CARRO, "Geely, EX5 Elétrico 2025", 2025, "Elétrico", "Automático", False, "Goiânia", "Goiás", 180000, "99 - FLAT LEVES E PICK-UPS (4%)", 35, "07", "DENTRO", True),
    (17, "BYD King na tabela leves", CARRO, "BYD, King DM-i GS Híbrido 2025", 2025, "Gasolina/Elétrico", "Automático", False, "Goiânia", "Goiás", 170000, "02 - LEVES (6%)", 35, "99", "DENTRO", True),
    (18, "Estado não autorizado", CARRO, "Fiat, Argo 1.0 Flex 2022", 2022, "Flex", "Manual", False, "Belém", "Pará", 70000, "01 - LEVES (5%)", 0, "—", "NEGAR", True),
    (19, "Cidade metropolitana SP", CARRO, "Fiat, Argo 1.0 Flex 2022", 2022, "Flex", "Manual", False, "Guarulhos", "São Paulo", 70000, "01 - LEVES (5%)", 0, "—", "NEGAR", True),
]


def brl(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def montar_card(c):
    n, desc, tipo, veiculo, ano, comb, cambio, uber, cidade, uf, valor, tabela, rastreador, *_ = c
    pct = decidir_tabela.re.search(r"\((\d+)%\)", tabela)
    cota = valor * int(pct.group(1)) / 100 if pct else 800.0  # moto: cota fixa por cc
    return {
        "placa": f"TST{n:04d}", "veiculo": veiculo, "criado_em": datetime.now().strftime("%d/%m/%Y"),
        "tipo_veiculo": tipo, "ano_fabricacao": str(ano), "combustivel": comb, "cambio": cambio,
        "veiculo_de_trabalho_taxi_uber": uber, "valor_fipe": f"R$ {brl(valor)}", "valor_protegido": f"R$ {brl(valor)}",
        "estado_circulacao": uf, "cidade_circulacao": cidade, "cooperativa": "TESTE", "implementos": "nenhum",
        "_tabela_no_card": tabela, "_cota_no_card": f"R$ {brl(cota)}", "_rastreador_no_card": f"R$ {brl(rastreador)}",
        "_dia_vencimento": "10", "_depreciacao": "Selecione",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--caso", type=int, action="append")
    ap.add_argument("--provedor", choices=["gemini", "claude"], default=config.ENV.get("ia_provedor", "gemini"))
    ap.add_argument("--sem-ia", action="store_true")
    ap.add_argument("--pausa", type=float, default=4, help="segundos entre chamadas (limite da faixa grátis)")
    args = ap.parse_args()
    casos = [c for c in CASOS if not args.caso or c[0] in args.caso]
    regras_texto = None if args.sem_ia else decidir_tabela.montar_regras()
    saida = config.PASTA_SAIDA / ("testes-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    saida.mkdir(parents=True, exist_ok=True)

    resultados, acertos_tabela, erros_pegos, com_erro = [], 0, 0, 0
    for c in casos:
        n, desc, *_, esperada, regional_esp, tem_erro = c
        card = montar_card(c)
        decisao = conf = None
        erro_ia = None
        if not args.sem_ia:
            try:
                decisao = decidir_tabela.perguntar_ao_modelo(regras_texto, card, args.provedor)
                conf = decidir_tabela.conferir(decisao, card)
            except (Exception, SystemExit) as e:
                erro_ia = str(e)[:200]
            time.sleep(args.pausa)
        itens = {**itens_da_ia(decisao, conf), **regras.verificar(card)}
        sit = situacao(itens)
        if decisao:
            acertou = (decisao["regional"].upper() == "NEGAR" == regional_esp) or decisao["tabela_codigo"] == esperada
        else:
            acertou = regional_esp == "NEGAR" and itens["regional_autorizada"][0] == "DIVERGENTE"
        acertos_tabela += acertou
        pegou = sit != "OK" if tem_erro else None
        com_erro += tem_erro
        erros_pegos += bool(pegou)
        problemas = [f"{k}: {s}" for k, (s, _) in itens.items() if s != "OK"]
        resultados.append({"caso": n, "descricao": desc, "card": card, "esperado": {"tabela": esperada, "regional": regional_esp},
                           "ia": decisao, "itens": itens, "situacao": sit, "acertou_tabela": acertou,
                           "erro_de_proposito": tem_erro, "erro_pego": pegou, "erro_ia": erro_ia})
        ia_txt = f"{decisao['regional']} / {decisao['tabela_codigo']}" if decisao else (erro_ia or "sem IA")
        print(f"{'✅' if acertou else '❌'} caso {n:2} {desc[:32]:32} | esperado {regional_esp}/{esperada:3} | IA {ia_txt[:30]:30} | "
              f"{sit:10} {'(erro pego)' if pegou else '(ERRO NÃO PEGO)' if tem_erro else ''} {'; '.join(problemas)[:90]}")

    salvar(saida, "resultado_testes.json", resultados)
    print(f"\nTabela/regional certa: {acertos_tabela}/{len(casos)} · erros de propósito pegos: {erros_pegos}/{com_erro}")
    print(f"Detalhes em {saida / 'resultado_testes.json'}")


if __name__ == "__main__":
    main()
