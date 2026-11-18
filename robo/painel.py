"""Painel provisório: acompanha a automação ao vivo no navegador (http://127.0.0.1:8765).

Só no próprio computador (127.0.0.1): mostra CNH, comprovante e dados pessoais, então não sai da máquina.
O demo chama etapa()/definir()/documento()/campo()/passo(); a página consulta /estado a cada segundo.
Incertezas, custos de IA e gravações bloqueadas são lidos ao vivo dos próprios módulos.
Rever uma execução (apresentação, sem CRM): python -m robo.painel robo/saida/<execução>  (lê o painel.json dela)
"""
import json
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORTA = 8765
_trava = threading.Lock()
ESTADO = {"versao": 0, "status": "rodando", "inicio": time.time(), "etapa": "", "etapas": [], "card": {}, "fotos": [],
          "documentos": {}, "ia_tabela": None, "ia_docs": None, "conferencia": [], "situacao": None, "propostas": [],
          "faltas": [], "telas": [], "vistoria": None, "fipe": None, "passos": {}, "plano": [], "lote": None, "log": []}
# o que é de UM card (o lote limpa entre um card e outro e guarda uma cópia de cada card conferido)
POR_CARD = {"etapa": "", "etapas": [], "card": {}, "fotos": [], "documentos": {}, "ia_tabela": None, "ia_docs": None, "conferencia": [],
            "situacao": None, "propostas": [], "faltas": [], "telas": [], "vistoria": None, "fipe": None, "passos": {}}
_ARQUIVOS = {}  # id → caminho das imagens que a página pode pedir (só estas)
ATIVO = False
_REVER = None  # execução gravada sendo revista (incertezas, custos... vêm do painel.json, não dos módulos)


def _mudou():
    ESTADO["versao"] += 1


def definir(chave, valor):
    if ATIVO:
        with _trava:
            ESTADO[chave] = valor
            _mudou()


def etapa(n, titulo):
    if not ATIVO:
        return
    with _trava:
        agora = time.time()
        if ESTADO["etapas"] and not ESTADO["etapas"][-1].get("fim"):
            ESTADO["etapas"][-1]["fim"] = agora
        ESTADO["etapas"].append({"n": str(n), "titulo": titulo, "inicio": agora})
        ESTADO["etapa"] = f"{n}. {titulo}"
        _mudou()


def passo(grupo, texto, feito=""):
    """Sub-passo ao vivo de uma etapa (ex.: cada consulta à FIPE). Fecha o anterior do grupo com o resultado 'feito'."""
    if not ATIVO:
        return
    with _trava:
        agora = time.time()
        lista = ESTADO["passos"].setdefault(grupo, [])
        if lista and lista[-1]["estado"] == "rodando":
            lista[-1].update(estado="ok", fim=agora, feito=feito or lista[-1].get("feito", ""))
        if texto:
            lista.append({"texto": texto, "estado": "rodando", "inicio": agora, "feito": ""})
        _mudou()


def passo_fim(grupo, estado="ok", feito=""):
    if not ATIVO:
        return
    with _trava:
        lista = ESTADO["passos"].get(grupo) or []
        if lista and lista[-1]["estado"] == "rodando":
            lista[-1].update(estado=estado, fim=time.time(), feito=feito or lista[-1].get("feito", ""))
            _mudou()


def novo_card():
    """Lote: limpa as seções do card anterior (lista do lote, log e custos continuam)."""
    if not ATIVO:
        return
    with _trava:
        ESTADO.update(json.loads(json.dumps(POR_CARD)), inicio_card=time.time())
        _mudou()


def guardar_card(caminho, extra=None):
    """Lote: grava a cópia do card que acabou (o botão "ver" do painel abre esta cópia)."""
    if not ATIVO:
        return
    with _trava:
        if ESTADO["etapas"] and not ESTADO["etapas"][-1].get("fim"):
            ESTADO["etapas"][-1]["fim"] = time.time()
        copia = {k: ESTADO[k] for k in POR_CARD} | {"plano": ESTADO["plano"], "inicio_card": ESTADO.get("inicio_card")} | (extra or {})
        Path(caminho).write_text(json.dumps(copia, ensure_ascii=False, default=str), encoding="utf-8")


def imagem(caminho):
    """Registra uma imagem que a página pode mostrar e devolve o id dela."""
    chave = str(len(_ARQUIVOS))
    _ARQUIVOS[chave] = Path(caminho)
    return chave


def fotos(pasta_fotos, lista):
    if ATIVO:
        definir("fotos", [{"id": imagem(pasta_fotos / f["arquivo"]), "rotulo": f["rotulo"]} for f in lista])


def documento(rotulo, dados, arquivo):
    if not ATIVO:
        return
    with _trava:
        ESTADO["documentos"][rotulo] = {**dados, "imagem": imagem(arquivo)}
        _mudou()


def vistoria_foto(rotulo, resposta):
    """Uma foto analisada pela IA (chega uma a uma, de várias threads)."""
    if not ATIVO:
        return
    with _trava:
        if ESTADO["vistoria"] is not None:
            ESTADO["vistoria"]["fotos"][rotulo] = resposta
            _mudou()


def campo(nome, estado, lido=""):
    """Progresso do preenchimento: proposto → digitado/confere/diverge/pulado."""
    if not ATIVO:
        return
    with _trava:
        for p in ESTADO["propostas"]:
            if p["nome"] == nome:
                p["estado"], p["lido"] = estado, lido
        _mudou()


def tela(caminho, titulo):
    if ATIVO:
        with _trava:
            ESTADO["telas"].append({"id": imagem(caminho), "titulo": titulo})
            _mudou()


class _Eco:
    """Copia tudo o que vai para o terminal para o log do painel."""

    def __init__(self, original):
        self.original, self.resto = original, ""

    def write(self, texto):
        self.original.write(texto)
        with _trava:
            self.resto += texto
            *linhas, self.resto = self.resto.split("\n")
            if linhas:
                ESTADO["log"] = (ESTADO["log"] + linhas)[-600:]
                _mudou()
        return len(texto)

    def flush(self):
        self.original.flush()

    def __getattr__(self, nome):
        return getattr(self.original, nome)


def _ao_vivo():
    if _REVER is not None:
        return _REVER
    from . import custo_ia, incertezas
    from .sessao import BLOQUEADOS, GRAVADOS

    soma = custo_ia.total()
    return {"incertezas": incertezas.ANOTADAS, "bloqueados": len(BLOQUEADOS), "gravados": len(GRAVADOS),
            "custos": [{"tarefa": t, "modelo": m, "usd": d} for t, m, d in custo_ia.DESTA_EXECUCAO],
            "custo_total_usd": soma, "dolar": custo_ia.DOLAR}


class _Pedido(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _enviar(self, corpo, tipo):
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if self.path == "/":
            return self._enviar((Path(__file__).parent / "painel.html").read_bytes(), "text/html; charset=utf-8")
        if self.path.startswith("/estado"):
            with _trava:
                dados = json.dumps({**ESTADO, **_ao_vivo(), "agora": time.time()}, ensure_ascii=False, default=str)
            return self._enviar(dados.encode(), "application/json")
        if self.path.startswith("/card/"):  # lote: cópia de um card já conferido
            try:
                linha = (ESTADO.get("lote") or {}).get("cards", [])[int(self.path[6:].split("?")[0])]
                return self._enviar(Path(linha["painel"]).read_bytes(), "application/json")
            except (ValueError, IndexError, KeyError, OSError, TypeError):
                pass
        if self.path.startswith("/img/"):
            caminho = _ARQUIVOS.get(self.path[5:].split("?")[0])
            if caminho and caminho.exists():
                tipo = "image/png" if caminho.suffix.lower() == ".png" else "image/jpeg"
                return self._enviar(caminho.read_bytes(), tipo)
        self.send_error(404)


def iniciar(abrir_navegador=True):
    global ATIVO
    # no Windows, reuse_address deixa dois painéis na mesma porta (o navegador cai no antigo): porta exclusiva
    ThreadingHTTPServer.allow_reuse_address = False
    servidor = None
    for porta in range(PORTA, PORTA + 10):  # outro painel ainda aberto (esperando Enter) → próxima porta
        try:
            servidor = ThreadingHTTPServer(("127.0.0.1", porta), _Pedido)
            break
        except OSError:
            continue
    if not servidor:
        print(f"   (painel: portas {PORTA}–{PORTA + 9} em uso; rodando sem painel)")
        return None
    ATIVO = True
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    sys.stdout = _Eco(sys.stdout)
    url = f"http://127.0.0.1:{porta}"
    print(f"   Painel ao vivo: {url}")
    if abrir_navegador:
        webbrowser.open(url)
    return url


def finalizar(status):
    """Marca o fim e grava o estado (painel.json) na pasta da execução."""
    if not ATIVO:
        return
    pasta = Path(ESTADO["pasta"]) if ESTADO.get("pasta") else None
    with _trava:
        if ESTADO["etapas"] and not ESTADO["etapas"][-1].get("fim"):
            ESTADO["etapas"][-1]["fim"] = time.time()
        ESTADO["status"] = status
        _mudou()
        if pasta:
            (pasta / "painel.json").write_text(json.dumps({**ESTADO, **_ao_vivo(), "_arquivos": {k: str(v) for k, v in _ARQUIVOS.items()}},
                                                          ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def rever(pasta, abrir_navegador=True):
    """Abre o painel de uma execução já gravada (painel.json), para apresentar sem rodar o robô de novo."""
    global _REVER
    salvo = json.loads((Path(pasta) / "painel.json").read_text(encoding="utf-8"))
    _ARQUIVOS.update({k: Path(v) for k, v in salvo.pop("_arquivos", {}).items()})
    _REVER = {k: salvo.pop(k) for k in ("incertezas", "bloqueados", "gravados", "custos", "custo_total_usd", "dolar") if k in salvo}
    url = iniciar(abrir_navegador)
    with _trava:
        ESTADO.update(salvo, versao=ESTADO["versao"] + 1, revendo=str(pasta))
    return url


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("uso: python -m robo.painel robo/saida/<pasta da execução>")
    print(rever(sys.argv[1]))
    input("   Painel aberto. Enter para encerrar.\n")
