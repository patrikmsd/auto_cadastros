"""Teste 1 (só leitura): filtro da semana → lista de cards → abre 1 card sem analista → salva campos e fotos.

Uso:  python -m robo.explorar            (produção, só leitura)
      python -m robo.explorar --placa ABC1D23   (escolhe o card)
"""
import argparse
import json
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from playwright.sync_api import sync_playwright

from . import config
from .sessao import BLOQUEADOS, SessaoExpirada, abrir, acesso_expirou, esperar, proteger

JS_CARDS = r"""(nomes) => {
  const cab = [...document.querySelectorAll('*')].find(e => e.children.length < 3 && /^Vistorias$/.test(e.textContent.trim()));
  if (!cab) return [];
  let col = cab;
  for (let i = 0; i < 6 && col; i++) { col = col.parentElement; if (col.innerText.split('\n').length > 10) break; }
  const linhas = col.innerText.split('\n').map(s => s.trim()).filter(Boolean);
  const cards = []; let cur = null;
  for (const l of linhas) {
    if (/^(Em aprovação|Retornada|Pendente|Aprovada|Reprovada|Agendada)$/.test(l)) { cur = {status: l, tags: []}; cards.push(cur); }
    else if (cur) {
      if (/^[A-Z]{3}-?\d[A-Z0-9]\d{2} - /.test(l)) { cur.placa = l.split(' - ')[0]; cur.veiculo = l.split(' - ').slice(1).join(' - '); }
      else if (/•/.test(l)) cur.criado = l.split('•')[1].trim();
      else if (!/^R\$|^\d+[mhd]$|encontrad/.test(l)) cur.tags.push(l);
    }
  }
  cards.forEach(c => c.analistas = c.tags.filter(t => nomes.includes(t)));
  return cards;
}"""

JS_CAMPOS = r"""(raiz) => {
  const out = [];
  raiz.querySelectorAll('input,select,textarea').forEach(f => {
    if (!f.offsetParent || f.type === 'hidden' || f.type === 'file') return;
    let rot = '', n = f;
    for (let i = 0; i < 4 && n && !rot; i++) {
      n = n.parentElement;
      const l = n && n.querySelector('label,.control-label,span,small,strong');
      if (l && !l.contains(f) && l.innerText.trim()) rot = l.innerText.trim();
    }
    let v = f.tagName === 'SELECT' ? (f.selectedOptions[0]?.text || '')
          : (f.type === 'checkbox' || f.type === 'radio') ? String(f.checked) : f.value;
    out.push({rotulo: rot.slice(0, 60), nome: f.name || f.id || '', tipo: f.type || f.tagName.toLowerCase(), valor: (v || '').trim().slice(0, 200)});
  });
  return out;
}"""


JS_FOTOS = r"""(raiz) => [...raiz.querySelectorAll('img')]
  .filter(i => i.offsetParent && /amazonaws|visto/.test(i.src))
  .map(i => {
    let rotulo = '', n = i;
    for (let k = 0; k < 4 && n && !rotulo; k++) { n = n.parentElement; const t = n && n.innerText && n.innerText.trim(); if (t) rotulo = t.split('\n')[0]; }
    return {rotulo: rotulo.slice(0, 60), url: i.src};
  })"""


def nome_arquivo(texto):
    base = texto.lower()
    for a, b in (("áàâã", "a"), ("éê", "e"), ("í", "i"), ("óôõ", "o"), ("ú", "u"), ("ç", "c")):
        for ch in a:
            base = base.replace(ch, b)
    return "".join(c if c.isalnum() else "_" for c in base).strip("_")[:40] or "foto"


def salvar(pasta, nome, dados):
    (pasta / nome).write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def modal_visivel(page):
    m = page.locator("div.modal:visible")
    return m.last if m.count() else page.locator("body")


def nova_saida():
    saida = config.PASTA_SAIDA / datetime.now().strftime("%Y%m%d-%H%M%S")
    saida.mkdir(parents=True, exist_ok=True)
    return saida


def gravar_rede(ctx):
    """Guarda o JSON que o próprio CRM recebe (fetchNegotiationCard, fetchQuotationCard…). Esvazie a lista entre cards."""
    rede = []

    def ao_responder(resp):
        try:
            if "json" in resp.headers.get("content-type", "") and "powercrm" in resp.url:
                rede.append({"url": resp.url, "status": resp.status, "json": resp.json()})
        except Exception:
            pass

    ctx.on("response", ao_responder)
    return rede


def listar_cards(page, saida):
    """Aplica o filtro da semana e lê a coluna Vistorias."""
    esperar(page, lambda: page.goto(config.url_filtro_semana(), wait_until="domcontentloaded"), maximo=10_000,
            pronto=lambda: page.evaluate(JS_CARDS, config.NOMES_ANALISTAS))
    cards = page.evaluate(JS_CARDS, config.NOMES_ANALISTAS)
    salvar(saida, "cards_filtro.json", cards)
    return cards


def buscar_card(page, codigo):
    """Digita o código (ou placa) no campo "Buscar..." do funil (#pipelineFilterSelector) e lê os cards que sobram."""
    campo = page.locator("#pipelineFilterSelector")
    esperar(page, lambda: page.goto(f"{config.BASE_URL}/company/pipeline?funnel={config.FUNIL_VENDAS}", wait_until="domcontentloaded"),
            maximo=8000, pronto=lambda: campo.is_visible() or acesso_expirou(page))
    if acesso_expirou(page):
        raise SessaoExpirada("o PowerCRM pediu para renovar o acesso: a sessão salva venceu")
    if not campo.is_visible():
        sem_permissao = page.get_by_text("sem permissão para visualizar este funil").first.is_visible()
        raise RuntimeError("o usuário da sessão está sem permissão para ver o funil de vendas" if sem_permissao
                           else "o campo Buscar do funil não apareceu")
    campo.fill(codigo)
    esperar(page, lambda: campo.press("Enter"), maximo=8000)
    return page.evaluate(JS_BUSCA, config.NOMES_ANALISTAS)


JS_BUSCA = r"""(nomes) => {
  // cada card do funil é <section id="<código>" class="pwrcrm-card">; o veículo fica em .vehicle-title,
  // "ABC1D23 - VW ..." ou só "VW - ..." quando o carro ainda não tem placa (zero km)
  const re = /^([A-Z]{3}-?\d[A-Z0-9]\d{2}) - /;
  return [...document.querySelectorAll('section.pwrcrm-card[id]')].filter(s => s.offsetParent).map(s => {
    const linhas = s.innerText.split('\n').map(x => x.trim()).filter(Boolean);
    const v = ((s.querySelector('.vehicle-title') || {}).innerText || '').trim();
    const m = v.match(re);
    const criado = linhas.find(l => /•/.test(l));
    return {codigo: s.id, placa: m ? m[1] : '', veiculo: m ? v.slice(m[0].length) : v,
            criado: criado ? criado.split('•')[1].trim() : '', tags: linhas, analistas: linhas.filter(l => nomes.includes(l))};
  });
}"""


def identificador(alvo):
    """Nome da pasta e das fotos do card: a placa, ou o código do card quando ainda não tem placa (zero km)."""
    return alvo.get("placa") or alvo.get("codigo") or "sem_placa"


def baixar_anexos(ctx, rede, pasta_fotos, inicio):
    """Anexos da aba Atividades: vêm no histórico do card (type 5) e baixam por GET em /internal/history/viewFile."""
    indice = []
    for r in rede:
        m = re.search(r"/internal/history/find/([\w-]+)", r["url"])
        if not m or not isinstance(r["json"], list):
            continue
        empresa = next((re.search(r"companyId=(\d+)", x["url"]).group(1) for x in rede if "companyId=" in x["url"]), "147")
        for item in r["json"]:
            if item.get("type") != 5:
                continue
            resp = ctx.request.get(f"{config.BASE_URL}/internal/history/viewFile?c={empresa}&q={m.group(1)}&h={item['id']}")
            if not resp.ok:
                continue
            ext = mimetypes.guess_extension(resp.headers.get("content-type", "").split(";")[0]) or ".jpg"
            ext = {".jpe": ".jpg", ".bin": ".jpg"}.get(ext, ext)
            inicio += 1
            arquivo = f"{inicio:02d}_anexo_{nome_arquivo(item.get('message') or 'arquivo')}{ext}"
            (pasta_fotos / arquivo).write_bytes(resp.body())
            indice.append({"arquivo": arquivo, "rotulo": f"Anexo (Atividades): {item.get('message') or 'sem descrição'}",
                           "anexo": True, "aba": "Atividades", "data": item.get("dateCreation")})
        break
    return indice


def pdf_em_imagens(corpo, maximo=4, escala=2):
    """Páginas do PDF como JPG (o OCR e a IA leem imagem). Até `maximo` páginas: documento digital tem 1 ou 2."""
    import cv2
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(corpo)
    try:
        paginas = []
        for i in range(min(len(pdf), maximo)):
            img = pdf[i].render(scale=escala).to_numpy()  # BGR(A), como o cv2 espera
            if img.ndim == 3 and img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            paginas.append(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes())
        return paginas
    finally:
        pdf.close()


ANEXOS_FILEPOND = {  # aba → (lista na rede, endereço de download, prefixo do arquivo)
    "Cotações": ("fetchQuotationAttachment", "/vwAttQttn?c={c}&n={n}&q={q}&f=", "cotacao"),
    "Contato": ("fetchAssociatedAttachment", "/vwAttAssc?c={c}&n={n}&f=", "contato"),
}


def baixar_anexos_cotacao(ctx, rede, pasta_fotos, inicio, aba="Cotações"):
    """Anexos das abas Cotações e Contato (FilePond): a lista vem em fetchQuotationAttachment / fetchAssociatedAttachment
    (nome em "text") e cada arquivo baixa por GET em /vwAttQttn ou /vwAttAssc, o mesmo endereço que a tela usa.
    Imagem da cotação fica no servidor com ".jpg" a mais no nome.
    PDF: o original fica na pasta (fora do índice) e cada página entra no índice como imagem."""
    lista, endereco, prefixo = ANEXOS_FILEPOND[aba]
    indice = []
    for r in rede:
        if lista not in r["url"] or not isinstance(r["json"], list):
            continue
        q = parse_qs(urlparse(r["url"]).query)
        empresa = next((re.search(r"companyId=(\d+)", x["url"]).group(1) for x in rede if "companyId=" in x["url"]), "147")
        base_url = config.BASE_URL + endereco.format(c=empresa, n=q["negotiationCode"][0], q=(q.get("quotationId") or [""])[0])
        for item in r["json"]:
            nome = item.get("text") or ""
            corpo = b""
            for tentativa in (nome, nome + ".jpg") if nome else ():
                resp = ctx.request.get(base_url + quote(tentativa))
                if resp.ok and resp.body():
                    corpo, tipo = resp.body(), resp.headers.get("content-type", "").split(";")[0]
                    break
            if not corpo:
                continue
            base, rotulo = nome_arquivo(Path(nome).stem), f"Anexo ({aba}): {nome}"
            if corpo[:5] == b"%PDF-":
                (pasta_fotos / f"{prefixo}_{base}.pdf").write_bytes(corpo)
                paginas = pdf_em_imagens(corpo)
                for n, jpg in enumerate(paginas, 1):
                    inicio += 1
                    arquivo = f"{inicio:02d}_anexo_{prefixo}_{base}_p{n}.jpg"
                    (pasta_fotos / arquivo).write_bytes(jpg)
                    indice.append({"arquivo": arquivo, "rotulo": rotulo + (f" (pág. {n})" if len(paginas) > 1 else ""),
                                   "anexo": True, "aba": aba, "pdf": f"{prefixo}_{base}.pdf"})
                continue
            ext = mimetypes.guess_extension(tipo) or ".jpg"
            ext = {".jpe": ".jpg", ".bin": ".jpg"}.get(ext, ext)
            inicio += 1
            arquivo = f"{inicio:02d}_anexo_{prefixo}_{base}{ext}"
            (pasta_fotos / arquivo).write_bytes(corpo)
            indice.append({"arquivo": arquivo, "rotulo": rotulo, "anexo": True, "aba": aba})
        break
    return indice


def ler_card(page, ctx, alvo, pasta, rede, busca=None, liberadas=False):
    """Abre um card SEM analista (só leitura), salva abas e fotos em `pasta`.
    Antes de clicar recarrega o filtro da semana, ou refaz a busca pelo código se `busca` vier preenchida.
    liberadas=True (só o assistente): abre também card com tag só de analistas de config.ANALISTAS_LIBERADAS."""
    if alvo["analistas"] and not (liberadas and config.card_liberado(alvo["analistas"])):
        raise ValueError(f"O card {identificador(alvo)} tem analista ({', '.join(alvo['analistas'])}): não será aberto.")
    pasta.mkdir(parents=True, exist_ok=True)
    rede.clear()
    if busca:
        buscar_card(page, busca)
    else:
        esperar(page, lambda: page.goto(config.url_filtro_semana(), wait_until="domcontentloaded"), maximo=10_000,
                pronto=lambda: page.get_by_text(alvo["placa"]).count() > 0)
    # pelo código do card quando houver (card sem placa não tem o texto da placa para clicar)
    clicar = (page.locator(f"section.pwrcrm-card[id='{alvo['codigo']}'] .vehicle-title").first if alvo.get("codigo")
              else page.get_by_text(alvo["placa"]).first)
    esperar(page, lambda: clicar.click(), maximo=6000, pronto=lambda: page.locator("div.modal:visible").count() > 0)
    modal = modal_visivel(page)
    (pasta / "card_atividades.txt").write_text(modal.inner_text(), encoding="utf-8")

    for aba in ("Contato", "Cotações", "Vistoria"):
        # "Contato" também é título de seção escondido no formulário: clica no primeiro texto igual que estiver visível
        esperar(page, lambda: modal.get_by_text(aba, exact=True).filter(visible=True).first.click(), maximo=4000)
        chave = aba.lower().replace("ç", "c").replace("õ", "o")
        salvar(pasta, f"card_{chave}.json", modal.evaluate(JS_CAMPOS))
        (pasta / f"card_{chave}.txt").write_text(modal.inner_text(), encoding="utf-8")
        try:  # a foto da tela é só registro: se travar esperando fonte, segue sem ela
            page.screenshot(path=str(pasta / f"card_{chave}.png"), full_page=True, timeout=15000)
        except Exception as erro:
            print(f"   (tela da aba {aba} não salva: {str(erro).splitlines()[0][:80]})")

    botao = modal.get_by_text("Visualizar fotos", exact=True)
    if botao.count():
        esperar(page, botao.first.click, maximo=8000)
    fotos = modal.evaluate(JS_FOTOS)

    pasta_fotos = config.PASTA_AMOSTRAS / identificador(alvo)
    pasta_fotos.mkdir(parents=True, exist_ok=True)
    indice = []
    for i, foto in enumerate(fotos, 1):
        resp = ctx.request.get(foto["url"])  # GET direto no S3 da vistoria
        if not resp.ok:
            continue
        ext = Path(urlparse(foto["url"]).path).suffix.lower() or ".jpg"  # o S3 responde octet-stream
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            ext = mimetypes.guess_extension(resp.headers.get("content-type", "").split(";")[0]) or ".jpg"
            ext = ".jpg" if ext == ".bin" else ext
        arquivo = f"{i:02d}_{nome_arquivo(foto['rotulo'])}{ext}"
        (pasta_fotos / arquivo).write_bytes(resp.body())
        indice.append({"arquivo": arquivo, "rotulo": foto["rotulo"]})
    indice += baixar_anexos(ctx, rede, pasta_fotos, len(indice))
    indice += baixar_anexos_cotacao(ctx, rede, pasta_fotos, len(indice))
    indice += baixar_anexos_cotacao(ctx, rede, pasta_fotos, len(indice), aba="Contato")
    salvar(pasta_fotos, "fotos.json", indice)
    salvar(pasta, "rede_json.json", rede)
    return pasta_fotos, len(indice)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--placa", help="placa do card a abrir (padrão: primeiro sem nome de analista)")
    ap.add_argument("--login-automatico", action="store_true", help="usa login/password do .env se não houver sessão salva")
    args = ap.parse_args()
    saida = nova_saida()

    with sync_playwright() as p:
        browser, ctx, page = abrir(p, login_automatico=args.login_automatico)
        proteger(ctx)
        rede = gravar_rede(ctx)

        print("1) Aplicando o filtro da semana:", config.url_filtro_semana())
        cards = listar_cards(page, saida)
        livres = [c for c in cards if not c["analistas"]]
        print(f"   {len(cards)} cards em Vistorias, {len(livres)} sem nome de analista")
        for c in cards:
            print(f"   - [{c['status']}] {c.get('placa')} {c.get('veiculo', '')[:40]} | analista: {', '.join(c['analistas']) or '—'}")

        alvo = next((c for c in cards if c.get("placa") == args.placa), None) if args.placa else (livres[0] if livres else None)
        if not alvo:
            print("Nenhum card livre para abrir. Fim.")
        elif alvo["analistas"]:
            print(f"O card {alvo['placa']} tem analista ({', '.join(alvo['analistas'])}): não será aberto.")
        else:
            print(f"2) Abrindo o card {alvo['placa']} (só leitura) e baixando as fotos")
            pasta_fotos, n = ler_card(page, ctx, alvo, saida, rede)
            print(f"   {n} fotos salvas em {pasta_fotos}")

        salvar(saida, "gravacoes_bloqueadas.json", BLOQUEADOS)
        print(f"\nPronto. Saída em {saida}")
        print(f"Gravações bloqueadas pela trava: {len(BLOQUEADOS)}")
        browser.close()


if __name__ == "__main__":
    main()
