"""Teste 3: a IA (Claude) decide em qual tabela do CRM o card se encaixa; o código confere a decisão.

Manda ao modelo SÓ dados do veículo (nada de nome, CPF, telefone).
As regras (tabela de aceitação + BASE REGRAS ROBO CADASTRO.xlsx) vão no system prompt com cache.

Uso:  python -m robo.decidir_tabela                 (último card lido pelo explorar.py)
      python -m robo.decidir_tabela --saida robo/saida/20261028-161808
Precisa de anthropic_api_key no .env.
"""
import argparse
import json
import re
from pathlib import Path

import anthropic
from openpyxl import load_workbook

from . import config

MODELO = config.ENV.get("claude_modelo", "claude-opus-5-5")  # ex.: claude-sonnet-5-5 (mais barato)
MODELO_GEMINI = "gemini-flash-latest"  # troque por gemini_modelo no .env; veja os disponíveis com --listar-modelos
PLANILHA = config.RAIZ / "BASE REGRAS ROBO CADASTRO.xlsx"
ACEITACAO = config.RAIZ / "robo" / "regras" / "tabela_aceitacao_22-05-2026.txt"
ABAS_REGRAS = ("ESCOLHA_TABELA", "TABELAS_CRM", "EXCECOES", "REGIONAL", "AJUSTES")

INSTRUCOES = """Você é analista de cadastro de uma associação de proteção veicular. Sua tarefa: dizer em qual
tabela cadastrada no PowerCRM o veículo do card deve estar.

Como decidir:
- O percentual de cota já vem na tabela do CRM. Você não calcula percentual: escolhe a tabela.
- Siga a ordem da aba ESCOLHA_TABELA: regional (cidade/UF de circulação, aba REGIONAL) → tipo → modelo com
  tabela própria → categoria na Tabela de Aceitação → combustível → uso (táxi/aplicativo).
- A Tabela de Aceitação diz a categoria do modelo (ex.: LEVES NORMAIS 4%). Considere faixas de ano e a linha
  "TODOS OS OUTROS" da marca quando o modelo não estiver listado.
- As decisões da aba EXCECOES valem sobre a Tabela de Aceitação.
- Use somente as regras abaixo. Se algo não estiver coberto, ou se faltar dado (ex.: combustível vazio),
  marque precisa_revisao_humana e explique o motivo.
- Mesmo com dado faltando, SEMPRE preencha tabela_codigo, tabela_nome e tabela_percentual com a tabela mais
  provável (deduza combustível/câmbio do nome do modelo: "Flex", "Diesel", "Aut.") e baixe a confiança.
  Deixe a tabela vazia só quando a regional for NEGAR.
- O nome da tabela deve ser exatamente como aparece na aba TABELAS_CRM daquela regional."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "regional": {"type": "string", "description": "DENTRO, FORA, PERNAMBUCO, SP FORA, SP2 FORA, RIBEIRÃO PRETO, CAMINHÕES ou NEGAR"},
        "motivo_regional": {"type": "string"},
        "linha_aceitacao": {"type": "string", "description": "linha da Tabela de Aceitação usada"},
        "excecoes_aplicadas": {"type": "array", "items": {"type": "string"}},
        "tabela_codigo": {"type": "string", "description": "ex.: 01"},
        "tabela_nome": {"type": "string", "description": "ex.: 01 - LEVES (5%)"},
        "tabela_percentual": {"type": "string", "description": "ex.: 5% ou fixo por cc"},
        "confianca": {"type": "string", "enum": ["alta", "media", "baixa"]},
        "precisa_revisao_humana": {"type": "boolean"},
        "motivo_revisao": {"type": "string"},
        "justificativa": {"type": "string"},
    },
    "required": ["regional", "motivo_regional", "linha_aceitacao", "excecoes_aplicadas", "tabela_codigo", "tabela_nome",
                 "tabela_percentual", "confianca", "precisa_revisao_humana", "motivo_revisao", "justificativa"],
    "additionalProperties": False,
}


def aba_como_texto(wb, nome):
    linhas = [" | ".join("" if c is None else str(c) for c in r) for r in wb[nome].iter_rows(values_only=True)]
    return f"<aba nome=\"{nome}\">\n" + "\n".join(linhas) + "\n</aba>"


def montar_regras():
    """Texto fixo e sempre na mesma ordem: assim o cache do prompt funciona entre um card e outro."""
    wb = load_workbook(PLANILHA, read_only=True, data_only=True)
    abas = "\n\n".join(aba_como_texto(wb, n) for n in ABAS_REGRAS)
    aceitacao = ACEITACAO.read_text(encoding="utf-8")
    return f"{INSTRUCOES}\n\n<planilha_regras>\n{abas}\n</planilha_regras>\n\n<tabela_aceitacao>\n{aceitacao}\n</tabela_aceitacao>"


def ultima_saida():
    pastas = sorted((p.parent for p in config.PASTA_SAIDA.glob("**/card_cotacoes.json")), key=lambda p: p.stat().st_mtime)
    if not pastas:
        raise SystemExit("Nenhum card lido ainda. Rode antes: python -m robo.explorar")
    return pastas[-1]


def valor_apos(texto, rotulo):
    m = re.search(rf"^{re.escape(rotulo)}\s*\n(.+)$", texto, re.M)
    return m.group(1).strip() if m else ""


def reais(texto):
    m = re.search(r"R\$\s*([\d.]+,\d{2})", texto or "")
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def dados_do_card(pasta):
    """Monta o resumo do veículo. Nenhum dado pessoal sai daqui."""
    campos = {c["nome"]: c["valor"] for c in json.loads((pasta / "card_cotacoes.json").read_text(encoding="utf-8")) if c["nome"]}
    rotulos = {c["rotulo"]: c["valor"] for c in json.loads((pasta / "card_cotacoes.json").read_text(encoding="utf-8")) if c["rotulo"]}
    texto = (pasta / "card_cotacoes.txt").read_text(encoding="utf-8")
    placa = (campos.get("vhclPlate") or rotulos.get("Placa", "")).upper()  # o consultor às vezes digita em minúsculas
    lista = pasta / "cards_filtro.json" if (pasta / "cards_filtro.json").exists() else pasta.parent / "cards_filtro.json"
    cards = json.loads(lista.read_text(encoding="utf-8"))
    card = next((c for c in cards if c.get("placa", "").replace("-", "").upper() == placa.replace("-", "")), {})
    return {
        "placa": placa,
        "veiculo": card.get("veiculo", ""),
        "criado_em": card.get("criado", ""),
        "tipo_veiculo": campos.get("vhclType", ""),
        "ano_fabricacao": rotulos.get("Ano Fabricação", ""),
        "combustivel": campos.get("vhclFuel", ""),
        "cambio": campos.get("vhclShft", ""),
        "veiculo_de_trabalho_taxi_uber": campos.get("qttnWorkVhcl", "") == "true",
        "valor_fipe": rotulos.get("Valor FIPE", ""),
        "valor_protegido": rotulos.get("Valor Protegido", ""),
        "estado_circulacao": valor_apos(texto, "Estado de circulação"),
        "cidade_circulacao": valor_apos(texto, "Cidade de circulação"),
        "cooperativa": valor_apos(texto, "Cooperativa"),
        "implementos": "nenhum" if "Nenhum implemento cadastrado" in texto else "ver card",
        # o que o consultor escolheu (vai para a conferência, não para o modelo)
        "_tabela_no_card": campos.get("qttnPlansTableSelect", ""),
        "_codigo_fipe": campos.get("vhclFipeCode", ""),
        "_cota_no_card": valor_apos(texto, "Cota de participação"),
        "_rastreador_no_card": valor_apos(texto, "Rastreador"),
        "_dia_vencimento": campos.get("vhclExprtnDay", ""),
        "_depreciacao": campos.get("vhclDepreciationValue", ""),
        # mensalidade de cada plano mostrada em Cotações → Planos ("PRATA + 2 opcionais / R$ 243,40 + R$ 50,00": vale o plano, sem os opcionais)
        "_planos": {p: reais("R$ " + v) for p, v in re.findall(r"^(OURO|PRATA|BRONZE)\b[^\n]*\nR\$\s*([\d.]+,\d{2})", texto, re.M)},
    }


def _gemini(sistema, conteudo, esquema):
    """Chama o Gemini (Google AI Studio) com saída em JSON. Chave: gemini_api_key / apigoogle no .env."""
    from google import genai
    from google.genai import errors, types

    client = genai.Client(api_key=config.ENV.get("gemini_api_key") or config.ENV.get("apigoogle") or config.ENV.get("api_ocr_google"))
    preferido = config.ENV.get("gemini_modelo", MODELO_GEMINI)
    # faixa grátis costuma dar 503 (sobrecarga) ou 429 (limite): tenta o próximo modelo da lista
    for modelo in dict.fromkeys([preferido, "gemini-flash-latest", "gemini-2.5-flash", "gemini-flash-lite-latest"]):
        try:
            resposta = client.models.generate_content(
                model=modelo,
                contents=conteudo,
                config=types.GenerateContentConfig(
                    system_instruction=sistema,  # texto fixo no início: o Gemini reaproveita como cache implícito
                    response_mime_type="application/json",
                    response_json_schema=esquema,
                ),
            )
            break
        except (errors.ServerError, errors.ClientError) as e:
            if e.code not in (429, 500, 503):
                raise
            print(f"   [{modelo}] indisponível ({e.code}), tentando o próximo...")
    else:
        raise SystemExit("Nenhum modelo Gemini respondeu agora. Tente de novo em alguns minutos.")
    config.ENV["gemini_modelo_usado"] = modelo
    u = resposta.usage_metadata
    from . import custo_ia

    custo_ia.registrar(esquema.get("title", "gemini"), modelo, u.prompt_token_count or 0, u.candidates_token_count or 0,
                       cache=u.cached_content_token_count or 0, raciocinio=u.thoughts_token_count or 0)
    if not resposta.text:
        raise SystemExit(f"Gemini não devolveu resposta: {resposta.candidates[0].finish_reason if resposta.candidates else resposta}")
    return json.loads(resposta.text)


def perguntar_gemini(regras, card):
    para_modelo = {k: v for k, v in card.items() if not k.startswith("_")}
    return _gemini(regras, "Card a classificar:\n" + json.dumps(para_modelo, ensure_ascii=False, indent=2), ESQUEMA)


INSTRUCOES_DOCS = """Você é analista de cadastro de uma associação de proteção veicular. Recebe os dados do veículo
cadastrados no card e o texto que o OCR leu de cada documento anexado (só as linhas sobre o veículo; nome e CPF foram
retirados). O OCR pode errar letras/números isolados (O/0, I/1, B/8): não acuse divergência por um caractere.
Para cada documento diga de que veículo ele é e se corresponde ao card (marca, modelo, espécie carro/moto, placa,
chassi, ano, combustível). Aponte observações graves do documento (chassi ou motor remarcado, sinistro, leilão,
restrição) e o que elas exigem no cadastro (remarcado/sinistro/leilão = depreciação de 30% no valor protegido;
alienação fiduciária é só informativa). Observação grave de um documento de OUTRO veículo não vale para o card.
Se houver mais de um documento do veículo e eles forem de veículos diferentes, diga qual corresponde ao card.
Responda só com o JSON pedido, em português."""

ESQUEMA_DOCS = {
    "type": "object",
    "properties": {
        "documentos": {"type": "array", "items": {"type": "object", "properties": {
            "origem": {"type": "string"},
            "veiculo_no_documento": {"type": "string"},
            "corresponde_ao_card": {"type": "string", "enum": ["sim", "nao", "inconclusivo"]},
            "divergencias": {"type": "array", "items": {"type": "string"}},
        }, "required": ["origem", "veiculo_no_documento", "corresponde_ao_card", "divergencias"]}},
        "observacoes_graves": {"type": "array", "items": {"type": "string"}},
        "documento_correto": {"type": "string"},
        "conclusao": {"type": "string"},
        "precisa_revisao_humana": {"type": "boolean"},
    },
    "required": ["documentos", "observacoes_graves", "documento_correto", "conclusao", "precisa_revisao_humana"],
}


def analisar_documentos(card, documentos, provedor="gemini"):
    """documentos: {origem: [linhas do OCR sobre o veículo]}. Só dados do veículo vão para a IA."""
    veiculo = {k: card[k] for k in ("placa", "veiculo", "tipo_veiculo", "ano_fabricacao", "combustivel", "_depreciacao")}
    conteudo = ("Veículo no card:\n" + json.dumps(veiculo, ensure_ascii=False, indent=2) +
                "\n\nTexto lido dos documentos:\n" + json.dumps(documentos, ensure_ascii=False, indent=2))
    if provedor == "gpt":
        from .gpt import perguntar_gpt_json

        return perguntar_gpt_json("comparar documentos", INSTRUCOES_DOCS, conteudo, ESQUEMA_DOCS)
    return _gemini(INSTRUCOES_DOCS, conteudo, ESQUEMA_DOCS)


def listar_modelos_gemini():
    from google import genai

    client = genai.Client(api_key=config.ENV.get("gemini_api_key") or config.ENV.get("apigoogle") or config.ENV.get("api_ocr_google"))
    for m in client.models.list():
        if "gemini" in m.name and "generateContent" in (m.supported_actions or []):
            print(m.name.removeprefix("models/"))


def perguntar_ao_modelo(regras, card, provedor):
    if provedor == "gemini":
        return perguntar_gemini(regras, card)
    if provedor == "gpt":
        from .gpt import perguntar_gpt_json

        para_modelo = {k: v for k, v in card.items() if not k.startswith("_")}
        return perguntar_gpt_json("decidir tabela", regras, "Card a classificar:\n" +
                                  json.dumps(para_modelo, ensure_ascii=False, indent=2), ESQUEMA)
    chave = config.ENV.get("anthropic_api_key")
    client = anthropic.Anthropic(api_key=chave) if chave else anthropic.Anthropic()
    para_modelo = {k: v for k, v in card.items() if not k.startswith("_")}
    resposta = client.beta.messages.create(
        model=MODELO,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # se o modelo recusar por política, a própria API tenta outro modelo
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": ESQUEMA}},
        system=[{"type": "text", "text": regras, "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
        messages=[{"role": "user", "content": "Card a classificar:\n" + json.dumps(para_modelo, ensure_ascii=False, indent=2)}],
    )
    if resposta.stop_reason == "refusal":
        raise SystemExit(f"O modelo recusou: {resposta.stop_details}")
    if resposta.stop_reason == "max_tokens":
        raise SystemExit("Resposta cortada (max_tokens)")
    texto = next(b.text for b in resposta.content if b.type == "text")
    u = resposta.usage
    print(f"   tokens: entrada {u.input_tokens} · cache gravado {u.cache_creation_input_tokens} · "
          f"cache lido {u.cache_read_input_tokens} · saída {u.output_tokens}")
    return json.loads(texto)


def conferir(decisao, card):
    """Conferências feitas por código (sem IA)."""
    wb = load_workbook(PLANILHA, read_only=True, data_only=True)
    tabelas = [(str(r[0]), str(r[1]), str(r[2]), str(r[3] or "")) for r in wb["TABELAS_CRM"].iter_rows(min_row=2, values_only=True)]
    regional = decisao["regional"].upper()
    existe = any(reg == regional and cod == decisao["tabela_codigo"] for reg, cod, _, _ in tabelas)

    m = re.match(r"(\d{2})\s*-", card["_tabela_no_card"])  # moto: "06 - MOTOCICLETAS (CC)" não tem %
    cod_card = m.group(1) if m else None
    m = re.search(r"\((\d+)%\)", card["_tabela_no_card"])
    pct_card = int(m.group(1)) if m else None
    pct_ia = re.search(r"(\d+)\s*%", decisao["tabela_percentual"])
    pct_ia = int(pct_ia.group(1)) if pct_ia else None

    nome = next((n for reg, cod, n, p in tabelas if reg == regional and cod == decisao["tabela_codigo"]
                 and (pct_ia is None or p == f"{pct_ia}%")), decisao["tabela_nome"])
    conf = {
        "tabela_existe_na_regional": existe or regional == "NEGAR",
        "tabela_esperada": f"{decisao['tabela_codigo']} - {nome} ({decisao['tabela_percentual']})",
        "tabela_no_card": card["_tabela_no_card"] or "(não lida)",
        "mesma_tabela": cod_card == decisao["tabela_codigo"] and (pct_ia is None or pct_ia == pct_card),
    }
    valor = reais(card["valor_protegido"])
    cota_card = reais(card["_cota_no_card"])
    if pct_ia and valor and cota_card:
        esperada = round(valor * pct_ia / 100, 2)
        conf["cota_esperada"] = esperada
        conf["cota_no_card"] = cota_card
        conf["cota_confere"] = abs(esperada - cota_card) < 0.05
        # até ~R$35 mil (e em moto) a cota é valor fixo da tabela, não % do valor: não dá para acusar erro
        if not conf["cota_confere"] and (valor <= 35_000 or "moto" in card["tipo_veiculo"].lower()):
            conf["cota_confere"] = None
            conf["cota_obs"] = "faixa de cota em valor fixo: conferir na tabela de preço"
    return conf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", help="pasta gerada pelo explorar.py (padrão: a mais recente)")
    ap.add_argument("--provedor", choices=["gemini", "gpt", "claude"],
                    default=config.ENV.get("ia_provedor", "gemini" if config.ENV.get("gemini_api_key") else "claude"))
    ap.add_argument("--listar-modelos", action="store_true", help="mostra os modelos Gemini liberados para a sua chave")
    ap.add_argument("--modelo", help="troca o modelo só nesta execução (ex.: gemini-2.5-flash)")
    args = ap.parse_args()
    if args.listar_modelos:
        return listar_modelos_gemini()
    if args.modelo:
        config.ENV["gemini_modelo" if args.provedor == "gemini" else "claude_modelo"] = args.modelo
    pasta = Path(args.saida) if args.saida else ultima_saida()

    card = dados_do_card(pasta)
    print(f"Card {card['placa']} · {card['veiculo']} · {card['cidade_circulacao']}/{card['estado_circulacao']}")
    print(f"1) Perguntando ao {args.provedor} qual tabela se encaixa...")
    try:
        decisao = perguntar_ao_modelo(montar_regras(), card, args.provedor)
    except anthropic.APIStatusError as e:
        raise SystemExit(f"Erro da API Anthropic ({e.status_code}): {e.message}")
    print("2) Conferindo a decisão por código...")
    conferencia = conferir(decisao, card)

    modelo = {"gemini": config.ENV.get("gemini_modelo_usado", MODELO_GEMINI),
              "gpt": config.ENV.get("gpt_modelo_usado")}.get(args.provedor, MODELO)
    resultado = {"card": card, "decisao_ia": decisao, "conferencia": conferencia, "modelo": modelo}
    (pasta / "decisao_tabela.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = conferencia["mesma_tabela"] and conferencia.get("cota_confere", True) is not False and not decisao["precisa_revisao_humana"]
    print(f"\n   Regional: {decisao['regional']} ({decisao['motivo_regional']})")
    print(f"   Aceitação: {decisao['linha_aceitacao']}")
    print(f"   Tabela esperada (IA): {conferencia['tabela_esperada']}  · confiança {decisao['confianca']}")
    print(f"   Tabela no card:       {conferencia['tabela_no_card']}")
    if "cota_esperada" in conferencia:
        print(f"   Cota: esperada R$ {conferencia['cota_esperada']:.2f} · card R$ {conferencia['cota_no_card']:.2f}")
    if decisao["precisa_revisao_humana"]:
        print(f"   ⚠ Revisão humana: {decisao['motivo_revisao']}")
    print(f"\n   RESULTADO: {'OK' if ok else 'VERIFICAR'}")
    print(f"   Justificativa: {decisao['justificativa']}")
    print(f"\nDetalhes em {pasta / 'decisao_tabela.json'}")


if __name__ == "__main__":
    main()
