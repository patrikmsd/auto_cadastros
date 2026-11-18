"""Demonstração do MVP: um comando faz tudo e mostra cada etapa no terminal, inclusive a análise da IA (ChatGPT; Gemini com --ia gemini).

  1. abre o PowerCRM (sessão salva, só leitura)
  2. acha o card pelo campo "Buscar..." do funil (código do card ou placa)
  3. lê os dados do card e baixa as fotos da vistoria
  4. OCR local (RapidOCR, nada sai do computador) nas fotos de documento × card
  4b. IA (gpt-6-luna) olha cada foto do veículo: modelo, tipo, cor, placa, painel, vidros, avarias, foto errada
  4c. FIPE oficial do mês da cotação: o modelo do card bate com CRLV, chassi e fotos?
  5. IA decide a tabela do CRM (só dados do veículo, nenhum dado pessoal)
  6. código confere tabela, cota e as regras
  7. veredito + relatório

Uso:  python -m robo.demo                       (card de teste 62qVR7BlEy)
      python -m robo.demo --codigo ABC1D23      (outro código ou placa)
      python -m robo.demo --sem-ocr
      python -m robo.demo --todas-imagens       (a IA olha também CNH e comprovante)
      python -m robo.demo --sem-fotos-ia        (sem a análise das fotos pela IA)
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

if not __package__:  # rodado como arquivo (py demo.py): registra a pasta do projeto para os imports do pacote robo
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "robo"

from playwright.sync_api import sync_playwright

from . import analisar_fotos, config, custo_ia, decidir_tabela, fipe, gpt, incertezas, painel, regras
from .conferir import ICONE, NOMES, escrever_relatorio, itens_da_ia, situacao
from .explorar import buscar_card, gravar_rede, ler_card, modal_visivel, nova_saida, salvar
from .sessao import BLOQUEADOS, abrir, proteger

CARD_TESTE = "62qVR7BlEy"
DOC_VEICULO = re.compile(r"documento do ve|crlv|chassi", re.I)
CAMPOS_IA = {"regional": "Regional", "motivo_regional": "Por quê", "linha_aceitacao": "Linha da tabela de aceitação",
             "excecoes_aplicadas": "Exceções aplicadas", "tabela_codigo": "Código da tabela", "tabela_nome": "Nome da tabela",
             "tabela_percentual": "Percentual", "confianca": "Confiança", "precisa_revisao_humana": "Pede revisão humana",
             "motivo_revisao": "Motivo da revisão", "justificativa": "Justificativa"}


def etapa(n, titulo):
    painel.etapa(n, titulo)
    print(f"\n{'═' * 78}\n {n}. {titulo}\n{'═' * 78}", flush=True)


def linha(rotulo, valor, largura=30):
    print(f"   {rotulo:<{largura}} {valor}")


def mascarar(cpf):
    d = re.sub(r"\D", "", cpf)
    return f"***.{d[3:6]}.{d[6:9]}-**" if len(d) == 11 else cpf


VEICULO = re.compile(r"\b(?:MARCA|MODELO|ESP[EÉ]CIE|TIPO|MOTOCICLETA|AUTOM[OÓ]VEL|CAMINHONETE|CAMIONETA|UTILIT|PASSAGEIRO|"
                     r"CARGA|[AÁ]LCOOL|GASOLINA|DIESEL|FLEX|EL[EÉ]TRIC|H[IÍ]BRID|REMARCAD|SINISTR|LEIL[AÃ]O|RESTRI|ALIENA|"
                     r"CILINDRADA|POT[EÊ]NCIA|ANO|COR|PRATA|PRETA|BRANCA|CINZA|VERMELH|AZUL|PLACA|CHASSI|RENAVAM|\w+/\w+)", re.I)  # \b: "ANO" não pega "CAETANO"
PESSOAL = re.compile(r"NOME|CPF|CNPJ|LOCAL|ENDERE|\d{3}\.?\d{3}\.?\d{3}-?\d{2}", re.I)


def linhas_veiculo(textos):
    """Só as linhas do CRLV sobre o veículo (vão para a IA): corta nome, CPF, local e as datas soltas."""
    fora = set()
    for i, t in enumerate(textos):  # a linha logo depois do rótulo NOME é o nome do proprietário
        if re.fullmatch(r"\W*NOME\W*", t.strip(), re.I):
            fora.add(i + 1)
    return [t for i, t in enumerate(textos) if i not in fora and VEICULO.search(t) and not PESSOAL.search(t)
            and not re.fullmatch(r"[\d/\s*.-]+", t)]


def ocr_documentos(pasta_card, pasta_fotos):
    """RapidOCR local nas fotos de documento: acha placa/chassi/renavam/CPF e compara com o card."""
    import cv2
    from rapidocr_onnxruntime import RapidOCR

    from .ocr_teste import PADROES, cpf_valido, ler_imagem, renavam_valido, valores_do_card

    card = valores_do_card(pasta_card)
    linha("Campos do card para comparar", ", ".join(f"{k}={v}" for k, v in card.items()) or "nenhum (placa/chassi/renavam vazios)")
    fotos = json.loads((pasta_fotos / "fotos.json").read_text(encoding="utf-8"))
    fotos = [f for f in fotos if f.get("anexo") or re.search(r"documento|cnh|crlv|comprovante|chassi", f["rotulo"], re.I)]
    ocr, resultado = RapidOCR(), {}
    for f in fotos:
        img = ler_imagem(pasta_fotos / f["arquivo"])
        if img is None:
            continue
        inicio = time.time()
        textos, em_pe = [], img
        for g in (img, cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE), cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)):
            lidas = [r[1] for r in (ocr(g)[0] or [])]
            if sum(map(len, lidas)) > sum(map(len, textos)):
                textos, em_pe = lidas, g
        # foto de lado: o giro que leu mais texto é o documento em pé. Guarda uma cópia endireitada para a IA
        # (deitada, a IA lê errado: "JOAO PINHEIRO", cidade de nascimento, virou o nome do associado)
        # só com bastante texto: selfie com 2 linhas de texto não diz de que lado está a foto
        endireitada = None
        if em_pe is not img and sum(map(len, textos)) >= 100:
            (pasta_card / "endireitadas").mkdir(exist_ok=True)
            endireitada = pasta_card / "endireitadas" / f"{Path(f['arquivo']).stem}.jpg"
            endireitada.write_bytes(cv2.imencode(".jpg", em_pe, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes())
        texto = " ".join(textos).upper()
        texto += " " + texto.replace(" ", "")
        achados = {k: sorted(set(re.findall(p, texto))) for k, p in PADROES.items()}
        achados["cpf"] = [mascarar(c) for c in achados["cpf"] if cpf_valido(c)]
        achados["renavam"] = [r for r in achados["renavam"] if renavam_valido(r)]
        achados["chassi"] = [c for c in achados["chassi"] if len(re.findall(r"[A-Z]", c[:11])) >= 3]  # VIN tem letras
        juntos = " ".join(textos).upper()
        colado = juntos.replace(" ", "")  # o OCR costuma grudar as palavras do cabeçalho
        e_crlv = "RENAVAM" in colado or "CERTIFICADODEREGISTRO" in colado  # anexo: a descrição é livre, vale o conteúdo
        e_cnh = not e_crlv and ("HABILITA" in colado or f["rotulo"].startswith("Documento CNH"))
        if not (DOC_VEICULO.search(f["rotulo"]) or e_crlv):  # CNH/comprovante: números soltos parecem placa/chassi
            achados = {k: v for k, v in achados.items() if k == "cpf"}
        conf = {}
        for chave, valor_card in card.items():
            lidos = [v.replace("-", "") for v in achados.get(chave, [])]
            if lidos:
                conf[chave] = "OK" if valor_card in lidos else f"DIVERGENTE (card {valor_card} × foto {', '.join(lidos)})"
        # o OCR às vezes separa "CHASSI" de "REMARCADO" em linhas diferentes
        remarcado = sorted(set(re.findall(r"(CHASSI|MOTOR)\W*REMARCADO", juntos))) or (["CHASSI/MOTOR"] if "REMARCADO" in juntos else [])
        if remarcado:
            achados["remarcado"] = remarcado
        tipo = "CRLV" if e_crlv else "CNH" if e_cnh else "outro"
        print(f"\n   📄 {f['rotulo']}  [{tipo}]  ({len(textos)} linhas de texto, {time.time() - inicio:.1f}s)")
        print(f"      amostra do texto: {' | '.join(textos[:6])[:150] or '(nenhum texto)'}")
        print(f"      reconhecido: {({k: v for k, v in achados.items() if v}) or 'nada'}")
        if conf:
            print(f"      × card: {conf}")
        if remarcado:
            print(f"      ⚠️  observação do documento: {', '.join(r + ' REMARCADO' for r in remarcado)}")
        resultado[f["rotulo"]] = {"linhas": len(textos), "achados": achados, "conferencia": conf, "crlv": e_crlv, "cnh": e_cnh,
                                  "linhas_veiculo": linhas_veiculo(textos) if e_crlv else [], "texto": textos,
                                  "arquivo": str(pasta_fotos / f["arquivo"]), "endireitada": str(endireitada or "")}
        painel.documento(f["rotulo"], {"tipo": tipo, "linhas": len(textos), "achados": achados, "conferencia": conf,
                                       "texto": textos, "segundos": round(time.time() - inicio, 1)}, pasta_fotos / f["arquivo"])
    if not fotos:
        print("   nenhuma foto de documento na vistoria")
    return resultado


CARD_LIBERADO = CARD_TESTE  # o único card em que o robô pode gravar (teste, não vai a lugar nenhum)
COMBUSTIVEL_CRLV = [("GASOLINA/EL", "Gasolina/Elétrico"), ("ALCOOL/GASOLINA", "Alcool/Gasolina"), ("DIESEL", "Diesel"),
                    ("ELETRIC", "Elétrico"), ("FLEX", "Flex"), ("GASOLINA", "Gasolina"), ("ALCOOL", "Etanol")]


def propor_preenchimento(card, pasta_card, ocr):
    """O que falta no card e dá para tirar do CRLV do próprio veículo (placa ou chassi batendo com o card)."""
    campos = {c["nome"]: c["valor"] for c in json.loads((pasta_card / "card_cotacoes.json").read_text(encoding="utf-8")) if c["nome"]}
    crlv = next(((r, d) for r, d in (ocr or {}).items() if d["crlv"] and "OK" in d["conferencia"].values()), None)
    propostas, faltas = [], []
    if not crlv:
        return propostas, ["nenhum CRLV do veículo do card para tirar os dados"]
    origem, d = crlv
    texto = " ".join(d["texto"]).upper()
    colado = texto.replace(" ", "")
    if not campos.get("vhclChassi") and d["achados"].get("chassi"):
        gravado = {c for r, x in ocr.items() if r.startswith("Número do chassi") for c in x["achados"].get("chassi", [])}
        chassi = next((c for c in d["achados"]["chassi"] if c in gravado), d["achados"]["chassi"][0])
        propostas.append(("vhclChassi", "Chassi", "", chassi, origem + (" = foto do chassi gravado" if chassi in gravado else "")))
        if chassi not in gravado:
            parecido = next((g for g in gravado if incertezas.iguais_parecidos(g, chassi)), None)
            incertezas.anotar(card["placa"], "Chassi", chassi, f"foto do chassi gravado leu {parecido}: difere só em caracteres "
                              "parecidos (1/T, 7/F...)" if parecido else "não confirmado pela foto do chassi gravado "
                              f"(leu: {', '.join(gravado) or 'nada'})", origem)
    if not campos.get("vhclEngnNmbr"):
        from rapidocr_onnxruntime import RapidOCR

        from . import extrair
        from .ocr_teste import ler_imagem

        motor = extrair.campos_crlv(RapidOCR(), ler_imagem(d["arquivo"]))[0].get("motor")
        if motor:
            propostas.append(("vhclEngnNmbr", "Número do motor", "", motor.replace("-", ""), origem))
            incertezas.anotar(card["placa"], "Número do motor", motor, "uma leitura só (OCR do CRLV), sem dígito verificador", origem)
    if not campos.get("vhclRenavam") and d["achados"].get("renavam"):
        validos = [r for r in d["achados"]["renavam"] if incertezas.renavam_valido(r)]
        renavam = (validos or d["achados"]["renavam"])[0]
        propostas.append(("vhclRenavam", "Renavam", "", renavam, origem + (" (dígito verificador OK)" if validos else "")))
        if not validos:
            incertezas.anotar(card["placa"], "Renavam", renavam, "dígito verificador não confere: OCR pode ter errado um número", origem)
    if not campos.get("vhclFbrctnYr"):
        ano_modelo = int(re.search(r"(19|20)\d{2}$", card["veiculo"]).group(0))
        anos = {int(a) for a in re.findall(r"\b(?:19|20)\d{2}\b", texto)}
        # fabricação = ano modelo ou o anterior; o CRLV traz os dois lado a lado
        ano = ano_modelo - 1 if ano_modelo - 1 in anos else ano_modelo if ano_modelo in anos else None
        if ano:
            propostas.append(("vhclFbrctnYr", "Ano Fabricação", "", str(ano), f"{origem} (ano modelo {ano_modelo})"))
            if ano == ano_modelo and ano_modelo - 1 not in anos:
                incertezas.anotar(card["placa"], "Ano Fabricação", str(ano), "deduzido: o CRLV lido só mostra o ano modelo", origem)
    if campos.get("vhclFuel") in ("", "Selecione"):
        opcao = next((o for chave, o in COMBUSTIVEL_CRLV if chave in colado), None)
        if opcao:
            propostas.append(("vhclFuel", "Combustível", "Selecione", opcao, origem))
    if campos.get("vhclShft") in ("", "Selecione"):
        nome = card["veiculo"].upper()
        if re.search(r"\bAUT|\bCVT\b", nome) or "CVT" in colado:
            propostas.append(("vhclShft", "Câmbio", "Selecione", "Automático", "nome do modelo na FIPE ('Aut.') / CRLV ('CVT')"))
        elif re.search(r"\bMEC|\bMANUAL", nome):
            propostas.append(("vhclShft", "Câmbio", "Selecione", "Manual", "nome do modelo na FIPE"))
    if campos.get("vhclExprtnDay") in ("", "Selecione"):
        liberado = regras.vencimento_liberado(card.get("cooperativa", ""))
        if liberado:
            propostas.append(("vhclExprtnDay", "Dia de Vencimento", "", liberado[0],
                              f"planilha VENCIMENTOS ({card['cooperativa']}): liberação desde {liberado[2]:%d/%m/%Y} → 1º boleto {liberado[1]}"))
        else:
            faltas.append(f"Dia de vencimento: associação '{card.get('cooperativa')}' não está na planilha de vencimentos")
    return [("Cotações", *x) for x in propostas], faltas


# campo do CRM (aba Contato) → (rótulo, campo extraído)
CONTATO_CNH = [("clntRegistration", "CPF", "cpf"), ("clntRG", "RG", "rg"), ("clntExpdt", "Órgão Expedidor", "orgao_expedidor"),
               ("clntExpdtDate", "Data Expedição", "emissao"), ("clntCNH", "CNH (nº registro)", "registro"),
               ("clntfrstQual", "Data 1ª habilitação", "primeira_habilitacao"), ("clntCNHExpir", "Validade CNH", "validade"),
               ("clntBirthdate", "Data Nascimento", "nascimento")]
CONTATO_ENDERECO = [("clntAddressZipcode", "CEP", "cep"), ("clntAddress", "Endereço", "logradouro"),
                    ("clntAddressNumber", "Número", "numero"), ("clntAddressComplement", "Complemento", "complemento"),
                    ("clntAddressNeighborhood", "Bairro", "bairro")]
SENSIVEIS = {"clntRegistration", "clntRG", "clntCNH"}
ENDERECO_AUTOMATICO = {"clntAddress", "clntAddressNeighborhood"}


def esconder(nome, valor):
    """No terminal, documento pessoal aparece só com os 3 últimos dígitos."""
    return re.sub(r"\d(?=(?:\D*\d){3})", "*", valor) if nome in SENSIVEIS else valor


def propor_contato(card, pasta_card, ocr):
    """Aba Contato: CNH (OCR local, pela posição dos rótulos) e comprovante de endereço. Só preenche campo vazio."""
    from rapidocr_onnxruntime import RapidOCR

    from . import extrair
    from .ocr_teste import ler_imagem

    atuais = {c["nome"]: c["valor"] for c in json.loads((pasta_card / "card_contato.json").read_text(encoding="utf-8")) if c["nome"]}
    propostas, faltas, motor = [], [], RapidOCR()
    cnh = next(((r, d) for r, d in (ocr or {}).items() if d["cnh"] and d["linhas"] >= 10), None)
    if cnh:
        dados, _ = extrair.campos_cnh(motor, ler_imagem(cnh[1]["arquivo"]))
        nome_card = re.sub(r"\W", "", regras.sem_acento(atuais.get("clntFullName", "")))
        nome_doc = re.sub(r"\W", "", regras.sem_acento(" ".join(cnh[1]["texto"])))
        if nome_card and nome_card not in nome_doc:
            faltas.append("o nome do card não aparece na CNH: confira se a CNH é do associado")
            incertezas.anotar(card["placa"], "CNH do associado", "", "o nome do card não aparece na CNH", cnh[0])
        if dados.get("registro") and not incertezas.cnh_valida(dados["registro"]):
            incertezas.anotar(card["placa"], "CNH (nº registro)", dados["registro"], "dígito verificador não confere", cnh[0], sensivel=True)
        if not incertezas.datas_cnh_coerentes(dados):
            incertezas.anotar(card["placa"], "Datas da CNH", " / ".join(dados.get(c, "?") for c in
                              ("nascimento", "primeira_habilitacao", "emissao", "validade")),
                              "fora de ordem (nascimento < 1ª habilitação ≤ emissão < validade): alguma data foi mal lida", cnh[0])
        if dados.get("rg"):
            incertezas.anotar(card["placa"], "RG", dados["rg"], "RG não tem dígito verificador padrão: conferir na CNH", cnh[0], sensivel=True)
        for nome, rotulo, chave in CONTATO_CNH:
            if dados.get(chave) and not atuais.get(nome):
                propostas.append(("Contato", nome, rotulo, "", dados[chave], cnh[0]))
            elif not dados.get(chave) and not atuais.get(nome):
                faltas.append(f"{rotulo}: não lido na CNH")
    else:
        faltas.append("nenhuma CNH legível para tirar CPF, RG e datas")
    comp = next(((r, d) for r, d in (ocr or {}).items() if r.lower().startswith("comprovante")), None)
    if comp:
        end = extrair.campos_endereco(comp[1]["texto"], card["cidade_circulacao"])
        numero = end.get("numero")
        if numero and not numero.isdigit():  # o campo Número só aceita dígitos: "C22" vai para o complemento
            end["complemento"] = " ".join(filter(None, [end.get("complemento"), f"CASA {numero}"]))
            end.pop("numero")
            faltas.append(f"Número: no comprovante é '{numero}' (não numérico), foi para o complemento")
            atuais["clntAddressNumber"] = "(ver complemento)"  # não repetir a falta abaixo
        if not end.get("cep") and end.get("logradouro"):
            uf = regras.UF.get(card["estado_circulacao"], card["estado_circulacao"])
            cep = extrair.buscar_cep(uf, card["cidade_circulacao"], end["logradouro"], end.get("bairro"))
            if cep:
                end["cep"] = cep
                comp_cep = f"ViaCEP: {end['logradouro']}, {end.get('bairro', '')} (o comprovante não traz o CEP do cliente)"
        for nome, rotulo, chave in CONTATO_ENDERECO:
            valor = end.get(chave)
            if chave == "cep" and valor and not atuais.get(nome) and "comp_cep" in locals():
                propostas.append(("Contato", nome, rotulo, "", valor, comp_cep))
                continue
            if valor and not atuais.get(nome):
                propostas.append(("Contato", nome, rotulo, "", valor, comp[0] + " (OCR: conferir a grafia)"))
                # rua e bairro que o ViaCEP achou iguais estão confirmados; o resto é só OCR
                if not ("comp_cep" in locals() and chave in ("logradouro", "bairro")):
                    incertezas.anotar(card["placa"], rotulo, valor, "OCR do comprovante, sem outra fonte para confirmar", comp[0])
            elif not valor and not atuais.get(nome):
                faltas.append(f"{rotulo}: não está no comprovante")
    if not atuais.get("clntPhoneMobile2"):  # regra interna: sem segundo celular, preencher com zeros
        propostas.append(("Contato", "clntPhoneMobile2", "Telefone celular 2", "", "00000000000", "regra: sem 2º celular, tudo 0"))
    if not atuais.get("clntGender"):
        faltas.append("Sexo: não consta na CNH")
    return propostas, faltas


def fechar_duplicata(page):
    """O CRM abre "Possível duplicata" ao digitar CPF/nome de cliente já existente: fecha (Cancelar) e segue.
    Duplicar não é problema aqui; "Escolher" trocaria o cliente do card, por isso nunca clica nele."""
    aviso = page.locator("div.modal:visible, [role=dialog]:visible").filter(has_text="Possível duplicata")
    if not aviso.count():
        return False
    aviso.last.get_by_role("button", name="Cancelar", exact=True).first.click()
    page.wait_for_timeout(800)
    print("   ↪️  aviso 'Possível duplicata' fechado (Cancelar)")
    return True


def responder_alteracoes(page, gravar, codigos):
    """Aviso "Salvar informações" (#pcrmModalChangesOverlay) ao trocar de aba com alteração não salva.
    Com --gravar: Salvar, liberando a trava só para o card de teste durante o clique.
    No ensaio: Descartar (nada foi gravado mesmo, e o aviso cobre a tela até alguém responder)."""
    from .sessao import LIBERAR_CARD

    aviso = page.locator("#pcrmModalChangesOverlay")
    if not (aviso.count() and aviso.first.is_visible()):
        return False
    botao = "Salvar" if gravar else "Descartar"
    if gravar:
        LIBERAR_CARD["codigos"] = set(codigos)
    try:
        aviso.first.get_by_role("button", name=botao, exact=True).first.click()
        page.wait_for_timeout(4000)
    finally:
        LIBERAR_CARD["codigos"] = None
    print(f"   ↪️  aviso 'Salvar informações' respondido: {botao}")
    return True


def abrir_aba(modal, page, aba, nome_campo, ao_trocar=lambda: None):
    """Clica na aba até o campo dela ficar visível. "Contato" também é título de seção dentro do formulário:
    o primeiro texto igual nem sempre é a aba, então tenta cada um que estiver visível."""
    campo = modal.locator(f"#{nome_campo}, [name={nome_campo}]").first
    for alvo in modal.get_by_text(aba, exact=True).all():
        if alvo.is_visible():
            alvo.click()
            page.wait_for_timeout(1500)
            ao_trocar()  # o CRM pode pedir para salvar/descartar o que mudou na aba anterior
            page.wait_for_timeout(3500)
            if campo.is_visible():
                return True
    return campo.is_visible()


def preencher_card(codigo, placa, propostas, gravar, saida):
    """Abre o card e, aba por aba, preenche e clica em Salvar. Sem --gravar a trava bloqueia e só mostra o envio."""
    from .sessao import GRAVADOS, LIBERAR_CARD

    if codigo != CARD_LIBERADO:
        raise SystemExit(f"Gravação só é permitida no card de teste {CARD_LIBERADO}.")
    with sync_playwright() as p:
        browser, ctx, page = abrir(p)
        proteger(ctx)
        rede = gravar_rede(ctx)
        buscar_card(page, codigo)
        page.get_by_text(placa).first.click()
        page.wait_for_timeout(6000)
        # o próprio card (tem "ID: <código>" no topo): não confunde com o aviso de duplicata, que também é um modal
        modal = page.locator("div.modal:visible").filter(has_text=f"ID: {codigo}").last
        antes, enviados = len(BLOQUEADOS), []
        for aba in dict.fromkeys(a for a, *_ in propostas):
            primeiro = next(x[1] for x in propostas if x[0] == aba)
            codigos = lambda: {codigo} | {re.search(r"cardId=(\d+)", r["url"]).group(1) for r in rede if "fetchQuotationCard?cardId=" in r["url"]}
            if not abrir_aba(modal, page, aba, primeiro, lambda: responder_alteracoes(page, gravar, codigos())):
                print(f"   ⚠️  aba {aba}: os campos não apareceram (tela salva em aba_{regras.sem_acento(aba).lower()}.png)")
                page.screenshot(path=str(saida / f"aba_{regras.sem_acento(aba).lower()}.png"), full_page=True)
            cotacao = next((re.search(r"cardId=(\d+)", r["url"]).group(1) for r in rede if "fetchQuotationCard?cardId=" in r["url"]), None)
            print(f"   aba {aba}:")
            for _, nome, rotulo, _, novo, _ in (x for x in propostas if x[0] == aba):
                campo = modal.locator(f"#{nome}, [name={nome}]").first
                if not campo.is_visible():
                    print(f"   ⏭️  {rotulo}: campo escondido na tela, pulado")
                    painel.campo(nome, "pulado")
                    continue
                ja = campo.evaluate("e => e.tagName === 'SELECT' ? '' : e.value").strip()
                if ja and nome in ENDERECO_AUTOMATICO:  # o CEP digitado antes já trouxe o endereço oficial dos Correios
                    print(f"   ↪️  {rotulo}: {ja} (preenchido pelo CRM a partir do CEP, mantido)")
                    painel.campo(nome, "confere", ja)
                    continue
                if campo.evaluate("e => e.tagName") == "SELECT":
                    campo.select_option(label=novo)
                else:  # campos com máscara (CPF, datas, CEP): digita como uma pessoa
                    campo.click()
                    campo.fill("")
                    campo.press_sequentially(novo, delay=40)
                    campo.press("Tab")
                    page.wait_for_timeout(500)
                    fechar_duplicata(page)
                    if nome == "clntAddressZipcode":
                        page.wait_for_timeout(4000)  # espera o CRM buscar o endereço do CEP
                lido = campo.evaluate("e => e.tagName === 'SELECT' ? e.selectedOptions[0].text : e.value")
                confere = re.sub(r"\W", "", lido) == re.sub(r"\W", "", novo)
                print(f"   ✏️  {rotulo}: {esconder(nome, lido)}" + ("" if confere else f"  ⚠️ esperado {esconder(nome, novo)}"))
                painel.campo(nome, "confere" if confere else "diverge", esconder(nome, lido))
            page.wait_for_timeout(1500)
            sufixo = regras.sem_acento(aba).lower()
            page.screenshot(path=str(saida / f"preenchido_{sufixo}.png"), full_page=True)
            painel.tela(saida / f"preenchido_{sufixo}.png", f"{aba}: preenchido")
            gravados_antes = len(GRAVADOS)
            if gravar:
                LIBERAR_CARD["codigos"] = {codigo} | ({cotacao} if cotacao else set())
            try:
                fechar_duplicata(page)
                modal.get_by_role("button", name="Salvar", exact=True).last.click()
                page.wait_for_timeout(3000)
                if fechar_duplicata(page):  # se o aviso abrir no Salvar, fecha e salva de novo
                    modal.get_by_role("button", name="Salvar", exact=True).last.click()
                page.wait_for_timeout(5000)
                fechar_duplicata(page)
                responder_alteracoes(page, gravar, codigos())
            finally:
                LIBERAR_CARD["codigos"] = None
            page.screenshot(path=str(saida / f"salvo_{sufixo}.png"), full_page=True)
            painel.tela(saida / f"salvo_{sufixo}.png", f"{aba}: depois do Salvar")
            enviados += GRAVADOS[gravados_antes:]
        enviados = enviados if gravar else BLOQUEADOS[antes:]
        salvar(saida, "envio_salvar.json", {"gravou": gravar, "requisicoes": enviados, "bloqueadas": BLOQUEADOS[antes:]})
        browser.close()
    return enviados


def main():
    """Roda a demo; com o painel, deixa a página aberta no fim para conferir com calma."""
    try:
        _main()
        painel.finalizar("concluido")
    except BaseException:
        painel.finalizar("erro")
        if painel.ATIVO:
            import traceback

            traceback.print_exc()
        else:
            raise
    if painel.ATIVO:
        try:
            input("\n   Painel continua aberto em http://127.0.0.1:8765 · Enter aqui para encerrar. ")
        except (EOFError, KeyboardInterrupt):
            pass


def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codigo", default=CARD_TESTE, help=f"código do card ou placa (padrão: card de teste {CARD_TESTE})")
    ap.add_argument("--sem-ocr", action="store_true", help="pula o OCR local das fotos")
    ap.add_argument("--login-automatico", action="store_true")
    ap.add_argument("--sem-fotos-ia", action="store_true", help="pula a análise das fotos da vistoria pela IA")
    ap.add_argument("--todas-imagens", action="store_true",
                    help="a IA analisa também CNH e comprovante (padrão: só as fotos do veículo)")
    ap.add_argument("--sem-painel", action="store_true", help="não abre o painel no navegador")
    ap.add_argument("--ia", choices=["gpt", "gemini"], default="gpt", help="quem analisa tabela e documentos (padrão: ChatGPT)")
    ap.add_argument("--modelo", help="troca o modelo só nesta execução (ex.: gpt-5.6-luna)")
    ap.add_argument("--preencher", action="store_true",
                    help=f"ensaio: preenche o que falta a partir do CRLV e clica em Salvar com a trava ligada (só {CARD_LIBERADO})")
    ap.add_argument("--gravar", action="store_true", help=f"com --preencher: grava de verdade (só no card de teste {CARD_LIBERADO})")
    args = ap.parse_args()
    if args.gravar and not args.preencher:
        raise SystemExit("--gravar só vale junto com --preencher")
    if args.preencher and args.codigo != CARD_LIBERADO:
        raise SystemExit(f"--preencher só é permitido no card de teste {CARD_LIBERADO}")
    if args.modelo:
        config.ENV["gpt_modelo" if args.ia == "gpt" else "gemini_modelo"] = args.modelo
        if args.ia == "gpt":
            gpt.MODELO = args.modelo
    nome_ia = "ChatGPT" if args.ia == "gpt" else "Gemini"

    def modelo_usado():
        return config.ENV.get("gpt_modelo_usado" if args.ia == "gpt" else "gemini_modelo_usado")

    inicio = time.time()
    saida = nova_saida()
    if not args.sem_painel:
        painel.iniciar()
        painel.definir("pasta", str(saida))
        painel.definir("plano", [["1", "Abrir o PowerCRM"], ["2", "Achar o card"], ["3", "Ler card e baixar fotos"],
                                 ["4", "OCR local dos documentos"]]
                       + ([] if args.sem_fotos_ia or args.ia != "gpt" else [["4b", "IA olha as fotos"]])
                       + [["4c", "FIPE oficial"], ["5", "IA decide a tabela"], ["5b", "IA compara documentos"],
                          ["6", "Conferência"], ["7", "Resultado"]]
                       + ([["8", "Preencher o card"]] if args.preencher else []))
    print(f"ROBÔ DE CONFERÊNCIA DE CADASTRO · MVP · {config.BASE_URL} · SÓ LEITURA (gravações bloqueadas)")

    # 1–3) PowerCRM: navegador aberto só o tempo de ler
    with sync_playwright() as p:
        etapa(1, "Abrindo o PowerCRM")
        browser, ctx, page = abrir(p, login_automatico=args.login_automatico)
        proteger(ctx)
        rede = gravar_rede(ctx)
        linha("Sessão", "ok")

        etapa(2, f'Buscando "{args.codigo}" no campo Buscar... do funil')
        achados = buscar_card(page, args.codigo)
        for c in achados:
            linha(f"{c['placa']}", f"{c['veiculo'][:45]} · criado {c['criado']} · analista: {', '.join(c['analistas']) or '—'}", 10)
        if not achados:
            browser.close()
            raise SystemExit("   Nenhum card encontrado com essa busca.")
        livres = [c for c in achados if not c["analistas"]]
        if not livres:
            browser.close()
            raise SystemExit("   O card tem nome de analista: não será aberto.")
        alvo = livres[0]
        salvar(saida, "cards_filtro.json", [alvo])

        etapa(3, f"Lendo o card {alvo['placa']} e baixando as fotos da vistoria")
        pasta_card = saida / alvo["placa"]
        pasta_fotos, n = ler_card(page, ctx, alvo, pasta_card, rede, busca=args.codigo)
        browser.close()
    card = decidir_tabela.dados_do_card(pasta_card)
    painel.definir("card", card)
    for chave, valor in card.items():
        linha(chave.lstrip("_").replace("_", " ") + (" (no card)" if chave.startswith("_") else ""), valor)
    fotos = json.loads((pasta_fotos / "fotos.json").read_text(encoding="utf-8"))
    painel.fotos(pasta_fotos, fotos)
    linha("Fotos baixadas", f"{n} → {pasta_fotos}")
    for f in fotos:
        print(f"      · {f['rotulo']}")

    # 4) OCR
    etapa(4, "OCR local dos documentos (RapidOCR, nada sai do computador)")
    ocr = None if args.sem_ocr else ocr_documentos(pasta_card, pasta_fotos)
    if args.sem_ocr:
        print("   pulado (--sem-ocr)")

    # 4b) IA olha cada foto da vistoria: veículo, painel, vidros, avarias, foto errada/escura
    vistoria = respostas = None
    if args.sem_fotos_ia or args.ia != "gpt":
        print("   (análise das fotos pela IA pulada" + (": --sem-fotos-ia)" if args.sem_fotos_ia else ": só com --ia gpt)"))
    else:
        quais = "todas as imagens (inclui CNH e comprovante)" if args.todas_imagens else "fotos do veículo"
        etapa("4b", f"{nome_ia} analisa as {quais} ({gpt.MODELO})")
        painel.definir("vistoria", {"fotos": {}, "resumo": None, "modelo": gpt.MODELO})
        t = time.time()
        respostas, _ = analisar_fotos.analisar(pasta_fotos, todas=args.todas_imagens, ao_terminar=painel.vistoria_foto)
        vistoria = analisar_fotos.resumir(respostas, card)
        painel.definir("vistoria", {"fotos": respostas, "resumo": vistoria, "modelo": gpt.MODELO,
                                    "segundos": round(time.time() - t, 1)})
        salvar(saida, "analise_fotos.json", {"resumo": vistoria, "fotos": respostas})
        v, pn = vistoria["veiculo"], vistoria["painel"]
        linha("Veículo nas fotos", " · ".join(x for x in (v["tipo"], v["marca"], v["modelo"], v["carroceria"], v["cor"]) if x) or "—")
        linha("Placas lidas", ", ".join(f"{p} ({n}x)" for p, n in vistoria["placas_lidas"].items()) or "nenhuma")
        if pn["fotos"]:
            linha("Painel", f"{'ligado' if pn['ligado'] else 'desligado' if pn['ligado'] is False else '?'}"
                            f" · km {', '.join(pn['km']) or '?'} · alertas: {', '.join(pn['luzes']) or 'nenhum'}"
                            + (f" · mensagens: {', '.join(pn['mensagens'])}" if pn["mensagens"] else ""))
        linha("Estado geral", ", ".join(f"{k} {n}x" for k, n in vistoria["estado_geral"].items()) or "—")
        for d in vistoria["vidros_danos"]:
            print(f"   🪟 vidro: {d['vidro']} · {d['tipo']} · {d.get('descricao', '')} (certeza {d.get('certeza', '?')}) · {d['foto']}")
            incertezas.anotar(card["placa"], f"Vidro · {d['vidro']}", d["tipo"], "IA viu na foto: confirmar olhando",
                              f"foto '{d['foto']}' ({gpt.MODELO})")
        for a in vistoria["avarias"]:
            print(f"   🔧 avaria: {a['local']} · {a['tipo']} · {a.get('gravidade', '')} (certeza {a.get('certeza', '?')}) · {a['foto']}")
            incertezas.anotar(card["placa"], f"Avaria · {a['local']}", f"{a['tipo']} ({a.get('gravidade', '')})",
                              "IA viu na foto: confirmar olhando (pode ser reflexo)", f"foto '{a['foto']}' ({gpt.MODELO})")
        for p in vistoria["fotos_com_problema"]:
            print(f"   📷 {p['foto']}: {p['motivo']}")
        for c in vistoria["cruzamento"]:
            print(f"   {ICONE[c['status']]} {c['item']} × card: {c['detalhe']}")
        linha("Tempo", f"{time.time() - t:.0f}s ({vistoria['fotos_analisadas']} imagens, 8 por vez)")

    # 4c) FIPE oficial (mês da cotação): card × FIPE e provas do carro eliminam as outras versões
    etapa("4c", f"FIPE oficial + {fipe.MODELO}: o modelo do card bate com o carro? (obrigatória)")
    try:
        conf_fipe = fipe.conferir(pasta_card, card, ocr, respostas, passo=lambda t, f="": painel.passo("fipe", t, f))
    except Exception as e:
        painel.passo_fim("fipe", "erro", str(e)[:120])
        conf_fipe = {"status": "NAO_VERIFICAVEL", "detalhe": f"OBRIGATÓRIA e não feita (site da FIPE indisponível: "
                                                             f"{str(e)[:150]}): conferir o modelo na FIPE manualmente"}
    painel.passo_fim("fipe", "erro" if conf_fipe["status"] == "NAO_VERIFICAVEL" else "ok", conf_fipe["detalhe"][:90])
    painel.definir("fipe", conf_fipe)
    salvar(saida, "fipe.json", conf_fipe)
    if conf_fipe.get("oficial"):
        o = conf_fipe["oficial"]
        if conf_fipe.get("mais_proximo"):
            mp = conf_fipe["mais_proximo"]
            linha("Busca pelo modelo", f"card sem código FIPE válido: '{mp['nome_no_card']}' → mais próximos: "
                                       + "; ".join(f"{x['modelo']} ({x['semelhanca']:.0%})" for x in mp["ranking"][:3]))
        linha("FIPE oficial", f"{o['CodigoFipe']} · {o['Modelo']} {o['AnoModelo']} {o['Combustivel']} · {o['Valor']} "
                              f"({conf_fipe['mes']}) · autenticação {o['Autenticacao']}")
        pv = conf_fipe["provas"]
        linha("Provas do carro", " · ".join(x for x in (pv["crlv_modelo"] and f"CRLV {pv['crlv_modelo']}",
                                                        pv["cilindrada"] and f"{pv['cilindrada']}cc", pv["combustivel"], pv["cambio"],
                                                        conf_fipe.get("ano_pelo_chassi") and f"chassi → {conf_fipe['ano_pelo_chassi']}")
                                             if x) or "nenhuma")
        if conf_fipe.get("ia"):
            ia = conf_fipe["ia"]
            linha(f"🤖 {fipe.MODELO}", f"{ia.get('versao') or '(não decidiu)'} · certeza {ia.get('certeza')} · card correto: "
                                      f"{ia.get('card_esta_correto')}")
            linha("Por quê", ia.get("justificativa", ""))
        for c in conf_fipe["candidatas"]:
            marca = "✅" if c.get("ia") == "possível" else "✖️ "
            print(f"      {marca} {c['modelo']} ({c['combustivel']})" + (f" · {c['valor']}" if c.get("valor") else "")
                  + (f" — IA: {c['ia']}" if c.get("ia") not in (None, "possível") else "")
                  + (f" · regras: {c['eliminada']}" if c["eliminada"] else " · regras: fica"))
    print(f"   {ICONE[conf_fipe['status']]} {conf_fipe['detalhe']}")
    if conf_fipe["status"] != "OK":
        incertezas.anotar(card["placa"], "Modelo FIPE", f"{conf_fipe['card']['codigo']} {conf_fipe['card']['modelo']}"
                          if conf_fipe.get("card") else card["veiculo"],
                          conf_fipe["detalhe"], f"FIPE oficial {conf_fipe['mes']} × CRLV/chassi/fotos")

    # 5) IA decide a tabela
    etapa(5, f"{nome_ia} decide a tabela do CRM")
    regras_texto = decidir_tabela.montar_regras()
    para_ia = {k: v for k, v in card.items() if not k.startswith("_")}
    linha("Regras enviadas", f"{len(regras_texto):,} caracteres (planilha BASE REGRAS + tabela de aceitação)".replace(",", "."))
    print("   Dados do veículo enviados (sem nome, CPF ou fotos):")
    print("      " + json.dumps(para_ia, ensure_ascii=False, indent=2).replace("\n", "\n      "))
    decisao = conf = erro = None
    try:
        t = time.time()
        decisao = decidir_tabela.perguntar_ao_modelo(regras_texto, card, args.ia)
        print(f"\n   🤖 Análise do {nome_ia} ({modelo_usado()}, {time.time() - t:.1f}s):")
        for chave, rotulo in CAMPOS_IA.items():
            valor = decisao.get(chave)
            if isinstance(valor, list):
                valor = "; ".join(valor) or "nenhuma"
            elif isinstance(valor, bool):
                valor = "sim" if valor else "não"
            if valor not in ("", None):
                linha(rotulo, valor, 28)
        conf = decidir_tabela.conferir(decisao, card)
        painel.definir("ia_tabela", {"decisao": decisao, "conferencia": conf, "modelo": modelo_usado(),
                                     "segundos": round(time.time() - t, 1)})
    except (Exception, SystemExit) as e:
        erro = str(e)[:300]
        painel.definir("ia_tabela", {"decisao": None, "erro": erro})
        print(f"   ❌ {nome_ia} falhou: {erro}")

    # 5b) IA compara os documentos (texto do OCR, só a parte do veículo) com o card
    analise_docs = None
    docs = {r: {"linhas": d["linhas_veiculo"], "placa_lida": d["achados"].get("placa", []), "chassi_lido": d["achados"].get("chassi", [])}
            for r, d in (ocr or {}).items() if d["crlv"] or r.startswith("Número do chassi")}
    if docs:
        etapa("5b", f"{nome_ia} compara os documentos com o card")
        print("   Texto enviado (só linhas sobre o veículo; nome e CPF retirados):")
        print("      " + json.dumps(docs, ensure_ascii=False, indent=2).replace("\n", "\n      "))
        try:
            t = time.time()
            analise_docs = decidir_tabela.analisar_documentos(card, docs, args.ia)
            painel.definir("ia_docs", analise_docs)
            print(f"\n   🤖 Análise do {nome_ia} ({modelo_usado()}, {time.time() - t:.1f}s):")
            for d in analise_docs["documentos"]:
                marca = {"sim": "✅", "nao": "❌", "inconclusivo": "❔"}[d["corresponde_ao_card"]]
                print(f"   {marca} {d['origem']}: {d['veiculo_no_documento']}")
                for div in d["divergencias"]:
                    print(f"         - {div}")
            for obs in analise_docs["observacoes_graves"]:
                print(f"   ⚠️  {obs}")
            linha("Documento que vale", analise_docs["documento_correto"], 20)
            linha("Conclusão", analise_docs["conclusao"], 20)
        except (Exception, SystemExit) as e:
            print(f"   ❌ {nome_ia} falhou: {str(e)[:300]}")

    # 6) conferência
    etapa(6, "Conferência por código (tabela, cota e regras)")
    itens = {**itens_da_ia(decisao, conf), **regras.verificar(card, pasta_fotos, ler_fotos=False)}
    if erro:
        itens["tabela"] = ("NAO_VERIFICAVEL", f"IA indisponível: {erro}")
    if ocr is not None:
        # CRLV "do card" = placa ou chassi lidos batem com o card; os outros são de outro veículo
        do_card = {r for r, d in ocr.items() if d["crlv"] and "OK" in d["conferencia"].values()}
        corretos = [r for r in do_card if r.startswith("Anexo")]
        divergentes = [f"{r}: {campo} {c}" for r, d in ocr.items() for campo, c in d["conferencia"].items() if c != "OK"]
        if divergentes and corretos:
            divergentes.append(f"o CRLV certo está em '{corretos[0]}': vincular na vistoria")
        sem_texto = [r for r, d in ocr.items() if d["linhas"] < 5 and not r.startswith(("Selfie", "Número do chassi"))]
        itens["fotos_documento"] = (("DIVERGENTE", "documento × card: " + "; ".join(divergentes)) if divergentes else
                                    ("ALERTA", "pouco ou nenhum texto: " + "; ".join(sem_texto)) if sem_texto else
                                    ("OK", "documentos com texto legível"))
        cpf_cnh = {c for d in ocr.values() if d["cnh"] for c in d["achados"].get("cpf", [])}
        cpf_crlv = {c for r in do_card for c in ocr[r]["achados"].get("cpf", [])}
        if cpf_cnh and cpf_crlv:
            itens["proprietario"] = (("OK", "CPF da CNH = CPF do proprietário no CRLV") if cpf_cnh & cpf_crlv else
                                     ("ALERTA", f"CPF da CNH ({', '.join(cpf_cnh)}) ≠ proprietário no CRLV ({', '.join(cpf_crlv)})"))
        remarcados = sorted({x for r in do_card for x in ocr[r]["achados"].get("remarcado", [])})
        if remarcados and card.get("_depreciacao") in ("", "Selecione", None):
            itens["remarcado"] = ("DIVERGENTE", f"documento diz {', '.join(r + ' REMARCADO' for r in remarcados)}, "
                                                "mas o card não tem depreciação de 30%")
    if analise_docs:
        errados = [d["origem"] for d in analise_docs["documentos"] if d["corresponde_ao_card"] == "nao"]
        itens["documentos_ia"] = (("DIVERGENTE", f"de outro veículo: {'; '.join(errados)} · {analise_docs['conclusao']}") if errados else
                                  ("ALERTA", analise_docs["conclusao"]) if analise_docs["precisa_revisao_humana"] or
                                  analise_docs["observacoes_graves"] else ("OK", analise_docs["conclusao"]))
    itens["fipe"] = (conf_fipe["status"], conf_fipe["detalhe"])
    if vistoria:
        itens["fotos_vistoria"] = analisar_fotos.conferencia(vistoria)
    painel.definir("conferencia", [{"nome": NOMES.get(k, k), "status": st, "detalhe": d} for k, (st, d) in itens.items()])
    for chave, (status, detalhe) in itens.items():
        print(f"   {ICONE[status]} {NOMES.get(chave, chave):<32} {detalhe}")

    # 7) veredito
    sit = situacao(itens)
    painel.definir("situacao", sit)
    resultado = {"card": card, "situacao": sit, "itens": itens, "decisao_ia": decisao, "conferencia_tabela": conf,
                 "modelo": modelo_usado() if decisao else None, "erro_ia": erro, "ocr": ocr,
                 "analise_documentos_ia": analise_docs, "vistoria_ia": vistoria, "fipe": conf_fipe}
    escrever_relatorio(saida, [resultado])
    etapa(7, "Resultado")
    marca = {"OK": "✅ OK – pode seguir", "VERIFICAR": "⚠️  VERIFICAR – precisa de olho humano",
             "DIVERGENTE": "❌ DIVERGENTE – erro no cadastro"}[sit]
    linha("Card", f"{card['placa']} · {card['veiculo']}")
    linha("Situação", marca)
    for chave, (status, detalhe) in itens.items():
        if status != "OK":
            print(f"      {ICONE[status]} {NOMES.get(chave, chave)}: {detalhe[:110]}")
    linha("Gravações bloqueadas", f"{len(BLOQUEADOS)} (nada foi alterado no CRM)")
    linha("Relatório", saida / "relatorio.md")
    if args.preencher:
        etapa(8, "Preencher o card com os dados dos documentos (CRLV, CNH, comprovante)" + ("" if args.gravar else " (ENSAIO: a trava bloqueia o Salvar)"))
        propostas, faltas = propor_preenchimento(card, pasta_card, ocr)
        contato, faltas_contato = propor_contato(card, pasta_card, ocr)
        propostas, faltas = propostas + contato, faltas + faltas_contato
        painel.definir("propostas", [{"aba": a, "nome": n, "rotulo": r, "atual": at, "novo": esconder(n, v), "origem": o,
                                      "estado": "proposto", "lido": ""} for a, n, r, at, v, o in propostas])
        painel.definir("faltas", faltas)
        for aba, nome, rotulo, atual, novo, origem in propostas:
            linha(f"[{aba}] {rotulo}", f"{atual or '(vazio)'} → {esconder(nome, novo)}   · fonte: {origem}", 34)
        for f in faltas:
            print(f"   ❔ não preenchido: {f}")
        if propostas:
            enviados = preencher_card(args.codigo, card["placa"], propostas, args.gravar, saida)
            print(f"\n   {'Gravado' if args.gravar else 'O Salvar enviaria'} ({len(enviados)} requisição(ões)):")
            for r in enviados:
                print(f"      {r['metodo']} {r['url'][:110]}")
                if r["corpo"]:
                    print(f"         corpo: {r['corpo'][:600]}")
            linha("Telas", f"{saida} (preenchido_*.png e salvo_*.png)")
            if args.gravar:
                print("   Rode a demo de novo (sem --preencher) para conferir o card atualizado.")
        else:
            print("   nada para preencher")
    if incertezas.ANOTADAS:
        etapa("!", f"Informações sem certeza ({len(incertezas.ANOTADAS)}): revisar antes de aprovar")
        for x in incertezas.ANOTADAS:
            print(f"   ⚠️  {x['campo']}: {x['valor'] or '-'} · {x['motivo']} · fonte: {x['fonte']}")
        incertezas.salvar(saida)
        linha("Registro", f"{incertezas.ARQUIVO} (acumula todas as execuções) · {saida / 'incertezas.json'}")
    if custo_ia.DESTA_EXECUCAO:
        etapa("$", "Custo de IA desta execução")
        for tarefa, modelo, dolares in custo_ia.DESTA_EXECUCAO:
            linha(tarefa, f"{modelo} · " + ("preço desconhecido" if dolares is None else
                                            f"US$ {dolares:.5f} (≈ R$ {dolares * custo_ia.DOLAR:.4f})"))
        soma = custo_ia.total()
        linha("TOTAL", "preço de algum modelo desconhecido" if soma is None else
              f"US$ {soma:.5f} (≈ R$ {soma * custo_ia.DOLAR:.4f}) · {len(custo_ia.DESTA_EXECUCAO)} chamada(s)")
        linha("Registro", custo_ia.ARQUIVO)
    linha("Tempo total", f"{time.time() - inicio:.0f}s")


if __name__ == "__main__":
    main()
