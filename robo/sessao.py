"""Abre o navegador com sessão salva e instala a trava de só leitura."""
import re
import time
from urllib.parse import urlparse

from . import config

BLOQUEADOS = []

# O PowerCRM usa POST até para LER. Só estes caminhos (mapeados em 28/10/2026) podem passar;
# qualquer outro POST/PUT/PATCH/DELETE é cancelado e registrado.
LEITURA_PERMITIDA = [re.compile(p) for p in (
    r"^/company/(initialConfigPipeline|fetchFavoriteFilters|fetchNegotiationCard|fetchQuotationCard|fetchCompanyTagGroups"
    r"|fetchAssociatedAttachment|fetchQuotationAttachment|companyImplements)$",
    r"^/company/quoteAdjustments/all$",
    r"^/internal/pipeline/(countNegotiations|fetchNegotiations)$",
    r"^/internal/history/find/[\w-]+$",
    r"^/api/activities/negotiation/[\w-]+$",
    r"^/api/proposals/history/[\w-]+$",
    r"^/api/negotiations/[\w-]+/stage-times$",
    r"^/api/(funnels/detailed|notifications/count)$",
    r"^/hivovaVisto/(fetchInspectionData|fetchInspectionFilesData)/\d+/[\w-]+$",
    r"^/powercrm/(relatedSalesman|companyInspectors|userCooperatives)$",
    r"^/(city/ct|vhclTypBdy|cb|bmy|cmby|cdn-cgi/rum)$",
)]


def leitura_permitida(url):
    caminho = urlparse(url).path
    return any(p.match(caminho) for p in LEITURA_PERMITIDA)


def esperar(page, acao=None, maximo=8000, quieto=700, pronto=None):
    """Faz a ação e espera o CRM terminar de responder, no lugar das pausas fixas.
    Segue quando nenhuma requisição está pendente há `quieto` ms (e `pronto()`, se vier, é verdadeiro).
    No pior caso espera `maximo` ms depois da ação, o mesmo tempo fixo de antes."""
    pendentes = set()
    eventos = {"request": lambda r: pendentes.add(r), "requestfinished": lambda r: pendentes.discard(r),
               "requestfailed": lambda r: pendentes.discard(r)}
    for nome, f in eventos.items():
        page.on(nome, f)
    try:
        if acao:
            acao()
        fim = time.monotonic() + maximo / 1000
        quieto_desde = time.monotonic()
        while time.monotonic() < fim:
            page.wait_for_timeout(100)
            if pendentes:
                quieto_desde = time.monotonic()
            elif time.monotonic() - quieto_desde >= quieto / 1000:
                try:
                    if not pronto or pronto():
                        return
                except Exception:  # a tela ainda está trocando: tenta de novo
                    pass
    finally:
        for nome, f in eventos.items():
            page.remove_listener(nome, f)


def arquivo_sessao():
    return config.PASTA_SESSAO / f"{urlparse(config.BASE_URL).hostname}.json"


class SessaoExpirada(Exception):
    """O painel inicial abre com a sessão salva, mas o funil mostra "Seu acesso expirou!" e esconde tudo."""


def acesso_expirou(page):
    return page.get_by_text("Seu acesso expirou").first.is_visible()


def esquecer_sessao():
    """Apaga a sessão vencida: o próximo abrir() cai no login com janela."""
    arquivo_sessao().unlink(missing_ok=True)


def _login_automatico(page):
    """Preenche e-mail e senha do .env (login / password) e clica em Acessar."""
    page.locator("input[type=email], input[name*=mail i], input[placeholder*=mail i]").first.fill(config.ENV["login"])
    page.locator("input[type=password]").first.fill(config.ENV["password"])
    page.get_by_role("button", name=re.compile("acessar", re.I)).first.click()


def _esperar_login(ctx, minutos=15):
    """Espera alguma aba chegar em /company/ (a escolha de empresa pode trocar ou abrir outra aba)."""
    fim = time.time() + minutos * 60
    while time.time() < fim:
        if not ctx.pages:
            raise SystemExit("A janela do navegador foi fechada antes do login terminar. Rode de novo e não feche a janela.")
        for aba in ctx.pages:
            if "/company/" in aba.url:
                return aba
        try:
            ctx.pages[-1].wait_for_timeout(1000)
        except Exception:  # a aba fechou durante a espera: confere as que sobraram
            pass
    raise SystemExit(f"Login não concluído em {minutos:g} minutos.")


def tem_credenciais():
    return bool(config.ENV.get("login") and config.ENV.get("password"))


def abrir(playwright, headless=True, login_automatico=False, _sem_janela_tentado=False):
    """Usa a sessão salva, sem janela (headless). Sessão vencida ou ausente:
    - login_automatico (e login/password no .env): entra sozinho SEM janela; se em 2 minutos não chegar ao CRM
      (ex.: pediu para escolher a empresa), abre a janela já com e-mail e senha digitados, para alguém terminar;
    - senão: abre a janela para o login manual."""
    estado = arquivo_sessao()
    automatico = login_automatico and tem_credenciais()
    browser = playwright.chromium.launch(headless=headless)
    ctx = browser.new_context(
        storage_state=str(estado) if estado.exists() else None,
        viewport={"width": 1500, "height": 900},
        accept_downloads=True,
    )
    page = ctx.new_page()
    esperar(page, lambda: page.goto(f"{config.BASE_URL}/company/dashboard", wait_until="domcontentloaded"), maximo=3000)
    if "/company/" not in page.url:
        if headless and not (automatico and not _sem_janela_tentado):  # login manual (ou o automático já falhou): precisa de janela
            browser.close()
            return abrir(playwright, headless=False, login_automatico=login_automatico, _sem_janela_tentado=True)
        if automatico:
            print(">>> Sessão do PowerCRM vencida: entrando com o login do .env" + (" (sem janela)" if headless else ""), flush=True)
            try:
                _login_automatico(page)
            except Exception as e:  # a tela de login mudou: segue para a janela
                print(f"   login automático não achou os campos: {str(e)[:120]}", flush=True)
                if headless:
                    browser.close()
                    return abrir(playwright, headless=False, login_automatico=login_automatico, _sem_janela_tentado=True)
        if headless:  # login automático sem janela: 2 minutos, depois cai para a janela
            try:
                page = _esperar_login(ctx, minutos=2)
            except SystemExit:
                print("   o login sem janela não chegou ao CRM: abrindo a janela para terminar", flush=True)
                browser.close()
                return abrir(playwright, headless=False, login_automatico=login_automatico, _sem_janela_tentado=True)
        else:
            print(">>> Aguardando o login (selecionar a empresa, se pedir). Até 15 minutos. Não feche a janela.", flush=True)
            page = _esperar_login(ctx)
        page.wait_for_timeout(3000)
        config.PASTA_SESSAO.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(estado))
        print(f"Sessão salva em {estado}")
    return browser, ctx, page


# Gravação liberada só durante o clique em Salvar do card de teste, e só se a requisição citar esse card.
# {"codigos": {"62qVR7BlEy", "45669780"}} enquanto liberado; None no resto do tempo.
LIBERAR_CARD = {"codigos": None}
GRAVADOS = []


def _do_card_liberado(request):
    codigos = LIBERAR_CARD["codigos"]
    if not codigos:
        return False
    texto = request.url + (request.post_data or "")
    return any(c in texto for c in codigos)


def proteger(ctx):
    """Cancela gravações no powercrm (fora a lista de leitura), exceto no beta em modo teste."""

    def handler(route, request):
        host = urlparse(request.url).hostname or ""
        grava = request.method not in ("GET", "HEAD", "OPTIONS")
        if grava and host.endswith("powercrm.com.br") and _do_card_liberado(request):
            GRAVADOS.append({"metodo": request.method, "url": request.url, "corpo": (request.post_data or "")[:5000]})
            print(f"  [GRAVANDO no card de teste] {request.method} {request.url[:120]}")
            return route.continue_()
        if grava and host.endswith("powercrm.com.br") and not config.PODE_GRAVAR and not leitura_permitida(request.url):
            BLOQUEADOS.append({"metodo": request.method, "url": request.url, "corpo": (request.post_data or "")[:5000]})
            print(f"  [bloqueado] {request.method} {request.url[:120]}")
            return route.abort()
        return route.continue_()

    ctx.route("**/*", handler)
