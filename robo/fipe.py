"""FIPE oficial (veiculos.fipe.org.br): o modelo do card bate com o carro, com certeza?

Vale a tabela do MÊS EM QUE A COTAÇÃO FOI FEITA (campo "criado em" do card), não a do mês atual.
1. card × FIPE: o código do card existe para o ano, o nome do modelo é o mesmo e o valor é o daquele mês.
   O código FIPE é obrigatório em todo card: a busca é SEMPRE pelo código do card. Card sem código, ou com código que
   não existe no ano-modelo do card → DIVERGENTE (não procura pelo nome do modelo).
2. versões concorrentes: todos os modelos da mesma marca e ano com o mesmo nome base (ex.: Civic EX, EXL, Sport).
3. QUEM DECIDE A VERSÃO É O gpt-6-luna: recebe as versões da FIPE (com valor) e as provas do carro — CRLV (versão,
   cilindrada, combustível, câmbio), chassi (ano-modelo), fotos (emblema) — e diz qual é, com que certeza e por quê.
   Os nomes no CRLV não seguem padrão ("EXLCVT", "EXL CVT", "EXL 2.0") e por isso a IA, não regra fixa, faz o casamento.
4. as regras de código fazem a contraprova: IA e regras discordam → alerta. Versão fora da lista da FIPE → ignorada.
Confirmado só com certeza alta e uma versão possível = a do card. Outra versão → divergente (sugere o código; nunca altera
o card). Mais de uma possível ou certeza média/baixa → sem certeza (vai para o registro de incertezas).
Etapa obrigatória: se a FIPE ou a IA falharem, o card fica para conferir (nunca passa como OK).
Para a FIPE só vão código FIPE, marca e ano; para a IA só dados do veículo (linhas do CRLV sem nome/CPF, chassi, fotos
já descritas). Respostas da FIPE ficam em cache (saida/fipe_cache.json).
"""
import difflib
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime

from . import analisar_fotos, config, gpt

API = "https://veiculos.fipe.org.br/api/veiculos/"
CACHE = config.PASTA_SAIDA / "fipe_cache.json"
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
TIPO = {1: "carro", 2: "moto", 3: "caminhao"}
COMBUSTIVEIS = {1: "Gasolina", 2: "Álcool", 3: "Diesel", 5: "Flex"}
ANO_VIN = "ABCDEFGHJKLMNPRSTVWXY123456789"  # 10º caractere do chassi: A=1980/2010 ... ciclo de 30 anos
# palavras que não distinguem versão (câmbio, combustível, portas e válvulas são conferidos à parte)
MODELO = config.ENV.get("fipe_modelo", "gpt-6-luna")
APELIDOS = {"VOLKSWAGEN": "VW", "CHEVROLET": "GM", "CHEV": "GM", "MERCEDES": "MERCEDES-BENZ", "MB": "MERCEDES-BENZ",
            "LAND": "LAND ROVER", "CITROEN": "CITROEN", "GWM": "GWM"}
COMUNS = {"FLEX", "AUT", "MEC", "AUT.", "MEC.", "4P", "2P", "5P", "16V", "8V", "12V", "24V", "SEDAN", "TB", "TURBO", "GASOLINA", "DIESEL"}

_cache = None


def _norm(t):
    # espaços sobrando não contam: a FIPE devolve "Saveiro Robust 1.6 Total Flex 16V " (com espaço no fim)
    texto = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().upper()
    return " ".join(texto.split())


def _post(endpoint, dados):
    global _cache
    if _cache is None:
        _cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    chave = endpoint + "?" + urllib.parse.urlencode(sorted(dados.items()))
    if chave not in _cache:
        pedido = urllib.request.Request(API + endpoint, data=urllib.parse.urlencode(dados).encode(), headers={
            "Content-Type": "application/x-www-form-urlencoded", "Referer": "https://veiculos.fipe.org.br/",
            "User-Agent": "Mozilla/5.0"})
        for tentativa in range(3):  # etapa obrigatória: insiste antes de desistir
            try:
                resposta = json.loads(urllib.request.urlopen(pedido, timeout=30).read())
                break
            except Exception:
                if tentativa == 2:
                    raise
                time.sleep(3 * (tentativa + 1))
        if endpoint == "ConsultarTabelaDeReferencia":
            return resposta  # muda todo mês: não guarda
        _cache[chave] = resposta
        CACHE.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
    return _cache[chave]


def veiculo_do_card(pasta_card):
    """Código FIPE, ano-modelo, marca e modelo como o CRM guarda (resposta do fetchQuotationCard)."""
    for r in json.loads((pasta_card / "rede_json.json").read_text(encoding="utf-8")):
        v = (r.get("json") or {}).get("vehicle") if "fetchQuotationCard" in r.get("url", "") else None
        if v and (v.get("fipeCode") or v.get("model")):
            ano = re.search(r"\d{4}", str(v.get("modelYearValue") or ""))
            return {"codigo": v.get("fipeCode") or "", "ano_modelo": int(ano.group()) if ano else None, "marca": v.get("brandName", ""),
                    "modelo": v.get("model", ""), "tipo": v.get("type") or 1, "valor": v.get("fipeValue", ""),
                    "atualizado_em": datetime.fromtimestamp(v["fipeLastUpdateDate"] / 1000).strftime("%d/%m/%Y")
                    if v.get("fipeLastUpdateDate") else ""}
    return None


def _do_titulo(card):
    """Sem o JSON do CRM: 'Honda, Civic Sedan EXL 2.0 Flex 16V Aut.4p 2017' → marca, modelo, ano."""
    m = re.match(r"\s*([^,]+),\s*(.+?)\s+((?:19|20)\d{2})?\s*$", card.get("veiculo") or "")
    if not m:
        return None
    return {"codigo": "", "ano_modelo": int(m.group(3)) if m.group(3) else None, "marca": m.group(1), "modelo": m.group(2),
            "tipo": 2 if re.search("moto", card.get("tipo_veiculo") or "", re.I) else 1, "valor": card.get("valor_fipe", ""),
            "atualizado_em": ""}


def _marca_fipe(base, *nomes):
    """Código da marca na FIPE pelo nome (card, CRLV 'HONDA/CIVIC', 'VW/GOL', 'I/...'): aceita apelido e prefixo."""
    for nome in nomes:
        tokens = re.findall(r"[A-Z0-9]+", _norm(APELIDOS.get(_norm(nome).split("/")[0].strip(), nome).split("/")[0]))
        if not tokens or tokens == ["I"]:
            continue
        for m in _post("ConsultarMarcas", base):
            rotulo = re.findall(r"[A-Z0-9]+", _norm(m["Label"]))
            if tokens[0] in rotulo or len(tokens[0]) >= 4 and any(r.startswith(tokens[0]) for r in rotulo):
                return m["Value"], m["Label"]
    return None, None


def _modelos_do_ano(base, marca, ano_modelo):
    todos = []
    for c in COMBUSTIVEIS:
        try:
            modelos = _post("ConsultarModelosAtravesDoAno", {**base, "codigoMarca": marca, "ano": f"{ano_modelo}-{c}",
                                                             "codigoTipoCombustivel": c, "anoModelo": ano_modelo})
        except Exception:
            continue
        for m in modelos if isinstance(modelos, list) else []:
            todos.append({"modelo": m["Label"], "codigo_modelo": m["Value"], "combustivel": COMBUSTIVEIS[c], "_c": c})
    return todos


def parecido(a, b):
    """0–1: palavras em comum (pesa mais) + semelhança das letras. 'CIVIC EXL CVT' × 'Civic Sedan EXL 2.0 Flex 16V Aut.4p'."""
    ta, tb = (set(re.findall(r"[A-Z0-9]+(?:\.[0-9])?", _norm(x))) for x in (a, b))
    if not ta or not tb:
        return 0.0
    comuns = len(ta & tb) / len(ta)  # quanto do nome do card aparece na FIPE
    return round(0.7 * comuns + 0.3 * difflib.SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio(), 3)


def tabela_do_mes(data_cotacao):
    """Código da tabela de referência do mês da cotação ('28/10/2026 - 17:42' → outubro/2026)."""
    d = datetime.strptime(data_cotacao[:10], "%d/%m/%Y")
    mes = f"{MESES[d.month - 1]}/{d.year}"
    for t in _post("ConsultarTabelaDeReferencia", {}):
        if t["Mes"].strip() == mes:
            return t["Codigo"], mes
    raise ValueError(f"tabela FIPE de {mes} não encontrada")


def _valor(dados):
    r = _post("ConsultarValorComTodosParametros", dados)
    if r.get("erro"):
        raise ValueError(f"FIPE: {r['erro']}")
    return r


def _valor_em_mes_seguinte(data_cotacao, valor_card, base, ano_modelo, ano, tipo, codigo):
    """(mês, valor) da primeira tabela DEPOIS do mês da cotação, até o mês atual, cujo valor é o do card; senão None."""
    d, hoje = datetime.strptime(data_cotacao[:10], "%d/%m/%Y"), datetime.now()
    m = d.month + 12 * d.year
    while m < hoje.month + 12 * hoje.year:
        m += 1
        try:
            tabela, mes = tabela_do_mes(f"01/{(m - 1) % 12 + 1:02d}/{(m - 1) // 12}")
            r = _valor({**base, "codigoTabelaReferencia": tabela, "anoModelo": ano_modelo, "codigoTipoCombustivel": ano.split("-")[1],
                        "tipoVeiculo": tipo, "modeloCodigoExterno": codigo, "tipoConsulta": "codigo"})
        except Exception:
            continue  # tabela do mês ainda não publicada
        if abs((_reais(r["Valor"]) or 0) - valor_card) < 1:
            return mes, r["Valor"]
    return None


def _reais(t):
    m = re.search(r"([\d.]+,\d{2})", t or "")
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def provas(ocr=None, vistoria_fotos=None):
    """O que o carro mostra: versão escrita no CRLV, cilindrada, combustível, câmbio, ano pelo chassi, emblema nas fotos."""
    p = {"crlv_modelo": "", "cilindrada": None, "combustivel": "", "cambio": "", "chassi": "", "fotos_versao": [], "fontes": []}
    for rotulo, d in (ocr or {}).items():
        if not (d.get("crlv") and "OK" in (d.get("conferencia") or {}).values()):
            continue  # só o CRLV que é deste carro (placa/chassi batem com o card)
        texto = [_norm(t).strip() for t in d.get("texto") or []]
        p["fontes"].append(rotulo)
        for t in texto:
            if not p["crlv_modelo"] and re.match(r"^(I/)?[A-Z]{2,12}/[A-Z0-9][A-Z0-9 .\-]{1,30}$", t) and \
                    not re.search(r"ALCOOL|GASOLINA|DIESEL|FLEX|ESPECIE|PLACA|COR|POTENCIA|CILINDRADA|MARCA|MODELO|VERSAO|ANTERIOR|UF$", t):
                p["crlv_modelo"] = t
            m = re.search(r"(\d{1,4})\s*CV\s*/\s*(\d{3,4})", t)
            if m and not p["cilindrada"]:
                p["cilindrada"] = int(m.group(2))
            if not p["combustivel"] and re.fullmatch(r"ALCOOL\s*/\s*GASOLINA|FLEX|GASOLINA|DIESEL|ALCOOL|ELETRICO", t):
                p["combustivel"] = "Flex" if "/" in t or t == "FLEX" else t.title()
        if re.search(r"\b(CVT|AUT|AT)\b|CVT$|AUT$", p["crlv_modelo"]):
            p["cambio"] = "automático (CRLV)"
        chassis = d.get("achados", {}).get("chassi") or []
        if chassis and not p["chassi"]:
            p["chassi"] = chassis[0]
    for rotulo, r in (vistoria_fotos or {}).items():
        if "erro" in r or analisar_fotos.e_documento(rotulo, r):
            continue
        detalhe = _norm((r.get("veiculo") or {}).get("versao_ou_detalhes"))
        if detalhe:
            p["fotos_versao"].append(detalhe)
        auto = (r.get("painel") or {}).get("cambio_automatico")
        if auto is not None and not p["cambio"]:
            p["cambio"] = ("automático" if auto else "manual") + f" (foto '{rotulo}')"
    return p


def _ano_pelo_chassi(chassi, perto_de):
    if len(chassi) != 17 or chassi[9] not in ANO_VIN:
        return None
    i = ANO_VIN.index(chassi[9])
    return min((1980 + i + 30 * k for k in range(3)), key=lambda a: abs(a - perto_de))


def conferir(pasta_card, card, ocr=None, vistoria_fotos=None, passo=lambda texto, feito="": None):
    """Devolve dict com oficial, candidatas, provas, status (OK/ALERTA/DIVERGENTE/NAO_VERIFICAVEL) e detalhe."""
    v = veiculo_do_card(pasta_card) or _do_titulo(card)
    if v and not v["codigo"]:
        v["codigo"] = card.get("_codigo_fipe", "")  # o que está no campo Código FIPE da tela
    if not v or not v["codigo"]:
        return {"status": "DIVERGENTE", "card": v, "detalhe": "card sem código FIPE: o código é obrigatório em todo card. "
                "Preencha o código e analise de novo"}
    pv = provas(ocr, vistoria_fotos)
    passo(f"Tabela de referência do mês da cotação ({card['criado_em'][:10]})")
    tabela, mes = tabela_do_mes(card["criado_em"])
    base = {"codigoTabelaReferencia": tabela, "codigoTipoVeiculo": v["tipo"]}
    tipo = TIPO.get(v["tipo"], "carro")
    res = {"mes": mes, "card": v, "consulta": f"{API}ConsultarValorComTodosParametros", "provas": pv}
    achados = []  # (status, texto)
    if not v["ano_modelo"] and pv["chassi"]:
        v["ano_modelo"] = _ano_pelo_chassi(pv["chassi"], int(card.get("ano_fabricacao") or 2015) + 1)
    if not v["ano_modelo"]:
        return {**res, "status": "NAO_VERIFICAVEL", "detalhe": "OBRIGATÓRIA e não feita: card sem ano-modelo"}
    ano_modelo = str(v["ano_modelo"])

    # 1) sempre pelo código FIPE do card (obrigatório): o código tem que existir no ano-modelo do card
    passo(f"Código {v['codigo']} em {ano_modelo} no site da FIPE", f"tabela de {mes}")
    anos = _post("ConsultarAnoModeloPeloCodigoFipe", {**base, "modeloCodigoExterno": v["codigo"]})
    ano = next((a["Value"] for a in anos if isinstance(a, dict) and a["Value"].startswith(ano_modelo)), None)
    if not ano:
        return {**res, "status": "DIVERGENTE", "detalhe": f"código FIPE {v['codigo']} do card não existe para {ano_modelo} em {mes} "
                f"(anos desse código: {', '.join(a['Label'] for a in anos if isinstance(a, dict)) or 'nenhum'}): "
                "confira o código FIPE e o ano-modelo do card"}
    oficial = _valor({**base, "anoModelo": ano_modelo, "codigoTipoCombustivel": ano.split("-")[1], "tipoVeiculo": tipo,
                      "modeloCodigoExterno": v["codigo"], "tipoConsulta": "codigo"})
    if _norm(oficial["Modelo"]) != _norm(v["modelo"]):
        achados.append(("DIVERGENTE", f"nome no card '{v['modelo']}' ≠ FIPE '{oficial['Modelo']}'"))
    res["oficial"] = oficial

    # versões concorrentes: modelos da mesma marca no ano
    passo(f"Marca '{oficial['Marca']}' na FIPE", f"{oficial['Modelo']} · {oficial['Valor']}")
    marca, nome_marca = _marca_fipe(base, *(x for x in (oficial["Marca"], v["marca"], pv["crlv_modelo"]) if x))
    if not marca:
        return {**res, "status": "NAO_VERIFICAVEL", "detalhe": f"OBRIGATÓRIA e não feita: marca '{v['marca']}' não achada na FIPE"}
    passo(f"Listando todos os modelos {nome_marca} {ano_modelo}", nome_marca)
    todos = _modelos_do_ano(base, marca, ano_modelo)
    vc, vo = _reais(card.get("valor_fipe") or v["valor"]), _reais(oficial["Valor"])
    if vc and vo and abs(vc - vo) >= 1:
        # o CRM atualiza o valor da cotação nos meses seguintes: vale qualquer tabela do mês da cotação até hoje
        depois = _valor_em_mes_seguinte(card["criado_em"], vc, base, ano_modelo, ano, tipo, v["codigo"])
        if depois:
            res["valor_atualizado"] = {"mes": depois[0], "valor": depois[1]}
        else:
            achados.append(("ALERTA", f"valor FIPE no card {card.get('valor_fipe') or v['valor']} ≠ FIPE de {mes} {oficial['Valor']}"
                                      " (nem de nenhum mês seguinte até hoje)"))

    passo("Versões concorrentes e valor oficial de cada uma", f"{len(todos)} modelos · referência: {oficial['Modelo']}")
    # 2) versões concorrentes: mesmo nome base (1ª palavra) + as mais parecidas com o nome do card (nome fora do padrão)
    nome_base = _norm(oficial["Modelo"]).split()[0]
    parecidas = {id(c) for c in sorted(todos, key=lambda c: -parecido(v["modelo"], c["modelo"]))[:8]}
    candidatas = [c for c in todos if _norm(c["modelo"]).split()[0] == nome_base or id(c) in parecidas]

    passo("Provas do carro (CRLV, chassi, fotos) e valor oficial de cada versão")
    # 3) provas eliminam versões
    motivos = {}

    def fora(c, motivo):
        motivos.setdefault(c["modelo"] + c["combustivel"], motivo)

    if pv["combustivel"]:
        for c in candidatas:
            if c["combustivel"] != pv["combustivel"]:
                fora(c, f"combustível {c['combustivel']} ≠ CRLV {pv['combustivel']}")
    if pv["cilindrada"]:
        litros = f"{round(pv['cilindrada'] / 1000, 1):.1f}"
        for c in candidatas:
            m = re.search(r"\b(\d\.\d)\b", c["modelo"])
            if m and m.group(1) != litros:
                fora(c, f"motor {m.group(1)} ≠ CRLV {pv['cilindrada']}cc ({litros})")
    if pv["cambio"]:
        auto = pv["cambio"].startswith("automático")
        for c in candidatas:
            if re.search(r"\bMEC\b", _norm(c["modelo"])) and auto or re.search(r"\bAUT\b", _norm(c["modelo"])) and not auto:
                fora(c, f"câmbio ≠ {pv['cambio']}")
    if pv["chassi"]:
        ano_vin = _ano_pelo_chassi(pv["chassi"], int(ano_modelo))
        res["ano_pelo_chassi"] = ano_vin
        if ano_vin and ano_vin != int(ano_modelo):
            achados.append(("DIVERGENTE", f"chassi indica ano-modelo {ano_vin}, card {ano_modelo}"))
    # versão: palavra que só algumas candidatas têm (EX, EXL, SPORT...) escrita no CRLV ou no emblema das fotos;
    # vence quem casa o trecho mais longo (EXL ganha de EX em "CIVICEXLCVT")
    vivas = [c for c in candidatas if c["modelo"] + c["combustivel"] not in motivos]
    # palavras com número contam (moto: 500X, 750X, 1000R). No CRLV as palavras vêm grudadas: vale pedaço de palavra;
    # nas fotos a descrição é uma frase: só palavra inteira ("NC" de NC 750X não pode casar com "INSCRICAO")
    palavras = [set(re.findall(r"[A-Z0-9]{2,}", _norm(c["modelo"]))) - COMUNS - {nome_base} for c in vivas]
    todas = set.intersection(*palavras) if palavras else set()
    crlv = re.sub(r"[^A-Z0-9]", "", pv["crlv_modelo"].split("/", 1)[-1])
    nas_fotos = {w for f in pv["fotos_versao"] for w in re.findall(r"[A-Z0-9]+", f)}
    notas = [max((len(p) for p in ps - todas if crlv and p in crlv or p in nas_fotos), default=0) for ps in palavras]
    if notas and max(notas) > 0:
        for c, n in zip(vivas, notas):
            if n < max(notas):
                fora(c, "versão ≠ " + " / ".join(x for x in (pv["crlv_modelo"] and f"CRLV '{pv['crlv_modelo']}'",
                                                             pv["fotos_versao"] and "emblema nas fotos") if x))
    for c in candidatas:
        c["eliminada"] = motivos.get(c["modelo"] + c["combustivel"], "")
    restam = [c for c in candidatas if not c["eliminada"]]
    for c in candidatas:
        if "valor" not in c:
            try:
                r = _valor({**base, "codigoMarca": marca, "codigoModelo": c["codigo_modelo"], "anoModelo": ano_modelo,
                            "codigoTipoCombustivel": c["_c"], "tipoVeiculo": TIPO.get(v["tipo"], "carro"), "modeloCodigoExterno": "",
                            "tipoConsulta": "tradicional"})
                c.update(valor=r["Valor"], codigo_fipe=r["CodigoFipe"])
            except Exception:
                c.update(valor="", codigo_fipe="")
    res["candidatas"] = [{k: x for k, x in c.items() if not k.startswith("_")} for c in candidatas]

    sem_prova = not pv["fontes"] and not pv["fotos_versao"]
    do_card = [c for c in restam if v["codigo"] and c.get("codigo_fipe") == v["codigo"] or _norm(c["modelo"]) == _norm(oficial["Modelo"])]
    regras = list(achados)  # contraprova: o que as regras concluiriam sozinhas
    if sem_prova:
        achados.append(("ALERTA", "sem CRLV deste carro: versão não confirmada por documento"))
    elif not restam:
        achados.append(("ALERTA", "nenhuma versão da FIPE bate com todas as provas (OCR pode ter errado): conferir"))
    elif len(restam) == 1 and not do_card:
        c = restam[0]
        achados.append(("DIVERGENTE", f"provas apontam '{c['modelo']}' (código {c.get('codigo_fipe')}, {c.get('valor')}), "
                                      f"card tem '{v['modelo']}' ({v['codigo'] or 'sem código'})"))
    elif len(restam) > 1:
        valores = [_reais(c.get("valor")) for c in restam if c.get("valor")]
        faixa = f" · diferença até R$ {max(valores) - min(valores):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if valores else ""
        achados.append(("ALERTA", f"não dá para distinguir entre {len(restam)} versões: "
                                  + "; ".join(f"{c['modelo']} {c.get('valor', '')}" for c in restam) + faixa))
    res["regras"] = {"restam": [c["modelo"] for c in restam],
                     "conclusao": " · ".join(t for _, t in achados[len(regras):]) or "só sobra a versão do card"}
    achados = regras  # a versão quem decide é a IA; as regras ficam como contraprova

    # 4) gpt-6-luna decide a versão
    passo(f"{MODELO} decide a versão pelas provas", f"{len(candidatas)} versões com valor · regras: {len(restam)} sobra(m)")
    try:
        ia = _perguntar_luna(v, oficial, mes, candidatas, pv, ocr, vistoria_fotos, res.get("ano_pelo_chassi"))
    except (Exception, SystemExit) as e:
        ia = None
        achados.append(("ALERTA", f"IA ({MODELO}) indisponível: {str(e)[:150]} · regras: {res['regras']['conclusao']}"))
    res["ia"] = ia
    certeza = False
    if ia:
        nomes = {_norm(c["modelo"]): c for c in candidatas}
        escolhida = nomes.get(_norm(ia.get("versao")))
        possiveis = [nomes[_norm(x)] for x in ia.get("versoes_possiveis") or [] if _norm(x) in nomes] or ([escolhida] if escolhida else [])
        descartes = {_norm(d.get("modelo")): d.get("motivo", "") for d in ia.get("descartadas") or []}
        for c in candidatas:
            c["ia"] = "possível" if c in possiveis else descartes.get(_norm(c["modelo"]), "descartada")
        e_do_card = escolhida is not None and (v["codigo"] and escolhida.get("codigo_fipe") == v["codigo"] or _norm(escolhida["modelo"]) == _norm(oficial["Modelo"]))
        if ia.get("versao") and escolhida is None:
            achados.append(("ALERTA", f"IA respondeu '{ia['versao']}', que não está na lista da FIPE: conferir"))
        elif sem_prova:
            achados.append(("ALERTA", "sem CRLV deste carro: versão não confirmada por documento"))
        elif len(possiveis) > 1 or ia.get("certeza") != "alta" or escolhida is None:
            valores = [_reais(c.get("valor")) for c in possiveis if c.get("valor")]
            faixa = (f" · diferença até R$ {max(valores) - min(valores):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                     if len(valores) > 1 else "")
            achados.append(("ALERTA", f"IA sem certeza ({ia.get('certeza')}): "
                                      + ("; ".join(f"{c['modelo']} {c.get('valor', '')}" for c in possiveis) or "nenhuma versão") + faixa
                                      + f" · {ia.get('justificativa', '')}"))
        elif not e_do_card:
            achados.append(("DIVERGENTE", f"IA aponta '{escolhida['modelo']}' (código {escolhida.get('codigo_fipe')}, "
                                          f"{escolhida.get('valor')}), card tem '{v['modelo']}' ({v['codigo'] or 'sem código'}) · {ia.get('justificativa', '')}"))
        else:
            certeza = True
        # contraprova: regras chegaram a uma versão só e não é a da IA
        if escolhida is not None and len(restam) == 1 and _norm(restam[0]["modelo"]) != _norm(escolhida["modelo"]):
            certeza = False
            achados.append(("ALERTA", f"IA ('{escolhida['modelo']}') e regras ('{restam[0]['modelo']}') discordam: conferir"))
    passo("Contraprova pelas regras e veredito", f"{(ia or {}).get('versao') or 'sem decisão'} · certeza {(ia or {}).get('certeza', '—')}")
    ordem = {"OK": 0, "ALERTA": 1, "DIVERGENTE": 2}
    res["status"] = max((s for s, _ in achados), key=ordem.get, default="OK")
    res["certeza"] = certeza and res["status"] == "OK"
    passo(None, {"OK": "confirmado", "ALERTA": "conferir", "DIVERGENTE": "divergente"}[res["status"]])
    res["detalhe"] = (" · ".join(t for _, t in achados) if achados else
                      f"confirmado pela IA ({MODELO}, certeza alta) e pelas regras: {oficial['Modelo']} {ano_modelo} "
                      f"{oficial['Combustivel']} · {oficial['Valor']} ({mes}) · {len(candidatas) - 1} outra(s) versão(ões) descartada(s)")
    if res.get("valor_atualizado"):
        res["detalhe"] += (f" · o valor do card ({res['valor_atualizado']['valor']}) é o da tabela de {res['valor_atualizado']['mes']}: "
                           "o CRM atualizou depois da cotação")
    res["achados"] = [{"status": s, "texto": t} for s, t in achados]
    res["candidatas"] = [{k: x for k, x in c.items() if not k.startswith("_")} for c in candidatas]
    return res


ESQUEMA = {"type": "object", "properties": {
    "versao": {"type": "string", "description": "nome EXATO de uma versão da lista, ou vazio se não der para saber"},
    "certeza": {"type": "string", "enum": ["alta", "media", "baixa"]},
    "versoes_possiveis": {"type": "array", "items": {"type": "string"}, "description": "nomes EXATOS da lista que as provas não descartam"},
    "descartadas": {"type": "array", "items": {"type": "object", "properties": {
        "modelo": {"type": "string"}, "motivo": {"type": "string"}}, "required": ["modelo", "motivo"]}},
    "card_esta_correto": {"type": "string", "enum": ["sim", "nao", "incerto"]},
    "justificativa": {"type": "string"},
    "pontos_de_atencao": {"type": "array", "items": {"type": "string"}}},
    "required": ["versao", "certeza", "versoes_possiveis", "descartadas", "card_esta_correto", "justificativa"]}

SISTEMA = """Você confere cadastro de veículo numa associação de proteção veicular.
Tarefa: dizer qual versão da TABELA FIPE OFICIAL é o veículo, usando só as PROVAS do carro (CRLV lido por OCR, chassi,
fotos descritas por outra IA). O que o card diz é a afirmação a conferir, não prova.
Regras:
- Escolha só nomes EXATOS da lista "versoes_fipe". Nunca invente versão.
- A referência é a versão do código FIPE do card. "card_esta_correto" = a versão das provas é essa referência.
- O CRLV escreve a versão sem padrão: palavras grudadas ("EXLCVT" = EXL + CVT), abreviadas, com câmbio (CVT/AT/AUT =
  automático; MT/MEC = manual) ou sem a versão. OCR troca letras parecidas (0/O, 1/I, 5/S, 8/B).
- Cilindrada em cc → litros (1997cc = 2.0; 1598 = 1.6; 999 = 1.0). "ALCOOL/GASOLINA" = Flex.
- Atenção a versões cujo nome contém outro (EX × EXL, LT × LTZ, SE × SEL): escolha a que bate com o texto inteiro.
- Descarte uma versão só com prova que a contradiga; explique o motivo de cada descarte em uma frase.
- certeza "alta" só se as provas deixam UMA versão possível. Se sobra mais de uma, liste todas em versoes_possiveis.
- Prova fraca (OCR ruim, foto com certeza baixa) não vale como certeza alta.
Responda só o JSON."""


def _perguntar_luna(v, oficial, mes, candidatas, pv, ocr, vistoria_fotos, ano_chassi):
    linhas_crlv = {r: d.get("linhas_veiculo") or [] for r, d in (ocr or {}).items() if r in pv["fontes"]}
    fotos = {r: {"o_que_mostra": x.get("o_que_a_foto_mostra", ""), "veiculo": x.get("veiculo"),
                 "cambio_automatico_no_painel": (x.get("painel") or {}).get("cambio_automatico"), "confianca": x.get("confianca")}
             for r, x in (vistoria_fotos or {}).items()
             if "erro" not in x and not analisar_fotos.e_documento(r, x) and not re.search(r"selfie", r, re.I)}
    conteudo = json.dumps({
        "card_afirma": {"codigo_fipe": v["codigo"], "modelo": v["modelo"], "marca": v["marca"], "ano_modelo": v["ano_modelo"]},
        "fipe_referencia_do_card": {k: oficial.get(k) for k in ("CodigoFipe", "Marca", "Modelo", "AnoModelo", "Combustivel", "Valor")},
        "mes_da_cotacao": mes,
        "versoes_fipe": [{"modelo": c["modelo"], "combustivel": c["combustivel"], "valor": c.get("valor", ""),
                          "codigo_fipe": c.get("codigo_fipe", "")} for c in candidatas],
        "provas": {"crlv_linhas_do_veiculo": linhas_crlv, "crlv_marca_modelo_versao": pv["crlv_modelo"],
                   "cilindrada_cc": pv["cilindrada"], "combustivel_crlv": pv["combustivel"], "cambio": pv["cambio"],
                   "chassi": pv["chassi"], "ano_modelo_pelo_chassi": ano_chassi, "fotos": fotos}},
        ensure_ascii=False)
    return gpt.perguntar_gpt_json("FIPE · versão do veículo", SISTEMA, conteudo, ESQUEMA, modelo=MODELO, esforco="low")


if __name__ == "__main__":
    import sys
    from pathlib import Path

    from . import decidir_tabela

    pasta = Path(sys.argv[1])
    print(json.dumps(conferir(pasta, decidir_tabela.dados_do_card(pasta)), ensure_ascii=False, indent=2))
