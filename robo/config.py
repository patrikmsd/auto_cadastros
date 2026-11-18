"""Configuração do robô: lê o .env da raiz e monta URLs e pastas."""
import os
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _ler_env():
    valores = {}
    arquivo = RAIZ / ".env"
    if arquivo.exists():
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.strip().startswith("#"):
                chave, valor = linha.split("=", 1)
                valores[chave.strip().lower()] = valor.strip().strip('"').strip()
    return valores


ENV = _ler_env()

URL_PRODUCAO = ENV.get("powercrm", "https://app.powercrm.com.br/").rstrip("/")
URL_TESTE = ENV.get("powercrm_beta", "https://beta.powercrm.com.br").rstrip("/")

# leitura = produção só leitura (padrão) | teste = beta, gravação liberada só lá
MODO = os.environ.get("ROBO_MODO", ENV.get("robo_modo", "leitura")).lower()
BASE_URL = URL_TESTE if MODO == "teste" else URL_PRODUCAO
PODE_GRAVAR = MODO == "teste" and "beta." in BASE_URL

FUNIL_VENDAS = "5018e464-ff6a-4663-a928-553468fc9228"
TAGS_ANALISAR = ["26770", "26990"]  # PENDENCIAS LEVES, CADASTRO COMPLETO
VISTORIA = ["3", "4"]  # Em aprovação, Retornadas

NOMES_ANALISTAS = [
    "ESTER", "ESTER DIAS", "JEFFERSON", "PAMMELA", "MARIA CLARA", "SILVIA", "LUANA", "CINTIA", "THAYNA",
    "HELOISE", "BARBARA", "CARINA", "GEOVANNA", "AMANDA", "VITORIA", "KEITY", "NICOLAS", "GUILHERME",
]

PASTA_SESSAO = RAIZ / "robo" / ".sessao"
PASTA_SAIDA = RAIZ / "robo" / "saida"
PASTA_AMOSTRAS = RAIZ / "testes" / "amostras"


def segunda_feira(hoje=None):
    hoje = hoje or date.today()
    return hoje - timedelta(days=hoje.weekday())


FILTRAR_TAGS = True  # False só para testes: semana inteira, sem exigir tag ANALISAR / status da vistoria


def _filtro_desde():
    """Data de criação inicial do filtro: filtro_desde= no .env (2020-01-01 ou 01/01/2020); padrão 01/01/2020."""
    from datetime import datetime

    texto = ENV.get("filtro_desde", "").strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            pass
    return date(2020, 1, 1)


FILTRO_DESDE = _filtro_desde()


def url_filtro_semana(inicio=None, fim=None):
    """Filtro do sistema: data de criação desde FILTRO_DESDE (01/01/2020) até hoje, tags PENDENCIAS LEVES / CADASTRO
    COMPLETO, vistoria Em aprovação + Retornadas. (O nome ficou de quando o filtro era só a semana.)"""
    inicio = inicio or FILTRO_DESDE
    fim = fim or date.today() + timedelta(days=1)
    url = f"{BASE_URL}/company/pipeline?dateBegin={inicio:%Y-%m-%d}&dateEnd={fim:%Y-%m-%d}&typeDate=0&tagAutomatic=0"
    if FILTRAR_TAGS:
        url += f"&tagDynamic={'+'.join(TAGS_ANALISAR)}&inspection={'+'.join(VISTORIA)}"
    return url + f"&funnel={FUNIL_VENDAS}"
