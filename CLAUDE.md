# Robô de conferência de cadastro · PowerCRM

Confere cards de cadastro de proteção veicular no PowerCRM: lê o card, baixa as fotos da vistoria, faz OCR local dos
documentos, usa IA (OpenAI `gpt-6-luna`) para fotos, FIPE, tabela e documentos, e aplica as regras da planilha
`BASE REGRAS ROBO CADASTRO.xlsx`. **No CRM o robô só lê**: quem preenche e salva é o analista.

## Peças

| Comando | Para quê |
|---|---|
| `python -m robo.assistente --rede` | **Produção.** Servidor com fila (porta 8780) + extensão do Chrome (`extensao/`) que mostra o resultado dentro do card |
| `python -m robo.lote` | Confere em lote os cards da semana no filtro das analistas (só leitura) |
| `python -m robo.demo` | Um card, etapa por etapa, com painel ao vivo (http://127.0.0.1:8765). Grava só no card de teste `62qVR7BlEy` |
| `python -m robo.painel robo/saida/<pasta>` | Revê uma execução gravada |
| `comparar_*`, `ocr_teste`, `testar_casos`, `gpt`, `explorar` | Scripts de teste e comparação, fora do fluxo de produção |

Rode sempre com `.venv\Scripts\python.exe` a partir da raiz, com `PYTHONIOENCODING=utf-8`.

## Servidor (produção)

Roda num **servidor Ubuntu sem tela** (headless) da rede local, como serviço systemd. Não existe janela de login:
o robô entra sozinho com `login`/`password` do `.env` e clica na empresa (`empresa=` no `.env`, padrão
`ANCORE PROTEÇÃO VEICULAR`, a que tem o funil de vendas `FUNIL_VENDAS`). Se o login falhar, o card dá erro com o motivo
e a foto da página fica em `robo/saida/logs/login_falhou-*.png`.

Requisitos: Ubuntu 24.04 (Python 3.12+; no 22.04 o instalador explica como pôr o 3.12), usuário comum com sudo.

1. Copiar o projeto (`git clone` ou cópia) e o `.env` (fora do Git) para o servidor.
2. Da raiz, com o usuário do serviço: `bash servidor/instalar.sh`. Ele instala as bibliotecas do sistema, o `.venv`, o
   Chromium do Playwright, cria e liga o serviço `assistente-powercrm` (religa sozinho 10 s depois de cair e liga junto com
   o servidor) e, se o `ufw` estiver ativo, libera a porta 8780 só para redes internas. Para atualizar o código, rode de novo.
3. Chave de cada analista: `.venv/bin/python -m robo.assistente --nova-chave "Nome"` (lista: `--chaves`). Fica em `robo/saida/assistente/analistas.json`.
4. Extensão em cada PC: `chrome://extensions` → Modo do desenvolvedor → Carregar sem compactação → pasta `extensao/`.
   O ícone da extensão abre a janelinha de conexão: endereço do servidor (ex.: `http://192.168.0.50:8780`) e a chave.

Comandos: `sudo systemctl status|restart|stop assistente-powercrm` · `journalctl -u assistente-powercrm -f`.
No PC de desenvolvimento (Windows) o mesmo código roda com tela; `ROBO_SEM_TELA=1` força o modo do servidor.

Logs: `robo/saida/logs/assistente-AAAAMMDD.log` (tudo o que sai no terminal, com hora). Custos de IA: `robo/saida/custos_ia.csv`.
Incertezas: `robo/saida/incertezas.csv`. A porta 8780 é HTTP sem criptografia: **nunca abrir para a internet**.

## Regras que não podem quebrar

- **Só leitura no CRM.** `robo/sessao.py` cancela todo POST/PUT/PATCH/DELETE fora da lista `LEITURA_PERMITIDA` (o PowerCRM
  usa POST até para ler). Gravação só no beta (`ROBO_MODO=teste`) ou no card de teste durante o clique em Salvar.
- Toda chamada de IA registra o custo (`robo/custo_ia.py`); modelo novo precisa entrar em `PRECOS`.
- Toda leitura sem certeza vai para `incertezas.anotar(...)`.
- FIPE: a do mês em que a cotação foi feita.
- Antes de mover ou refatorar estrutura, explicar em linguagem simples e esperar aprovação.

## Configuração (`.env` na raiz, fora do Git)

`powercrm`, `login`, `password` (login automático do robô), `api_gpt`, `api_mistral`, `apigoogle`, `anthropic_api_key`,
opcionais `gpt_modelo`, `fipe_modelo`, `dolar`, `robo_modo`.

Dados que mudam e precisam ser atualizados à mão: `robo/regras/tabela_aceitacao_22-05-2026.txt`, `NOMES_ANALISTAS` em
`robo/config.py` (analista nova sem nome ali → o robô abre os cards dela), as planilhas da raiz e o preço do `gpt-6-luna`
em `robo/custo_ia.py` (marcado como não confirmado: conferir na fatura).

## Histórico de mudanças

### 2026-11-18 · servidor Ubuntu sem tela

- **Login 100% sem janela** (`robo/sessao.py`): depois do e-mail e senha o PowerCRM mostra "selecione a empresa"
  (`/internal/changeCompany`); o robô agora clica na empresa do `.env` pela página. No servidor sem tela (`SEM_TELA`:
  Linux sem `DISPLAY`, ou `ROBO_SEM_TELA=1`) nunca tenta abrir janela: se não chegar ao CRM em 3 minutos, o card dá erro
  com o motivo e a foto da página. Testado no PowerCRM real: login + empresa em 10 s, reaproveitando a sessão em 3 s, e o
  caminho de falha (empresa inexistente) dando o erro certo.
- Trava de só leitura libera `/cdn-cgi/challenge-platform/` (verificação da Cloudflare, não é dado do CRM).
- `servidor/instalar.sh` + serviço systemd no lugar do `.bat`/`.ps1` do Windows (removidos).
- `robo/requirements.txt` com as versões fixadas; o instalador deixa só o `opencv-python-headless`.
- `robo/assistente.py`: uma f-string que só abria no Python 3.12 foi reescrita (todos os módulos abrem a partir do 3.10;
  as bibliotecas fixadas pedem 3.12).
- Não testado: a instalação completa num Ubuntu 24.04 (o WSL desta máquina é 22.04, sem sudo).

### 2026-11-18 · preparação para rodar no servidor

- **Login automático do CRM** (`robo/sessao.py`): com a sessão vencida, o robô entra sozinho, sem janela, com `login`/`password`
  do `.env`. Se em 2 minutos não chegar ao CRM (ex.: pediu para escolher a empresa), abre a janela com e-mail e senha já
  digitados para alguém terminar (até 15 min). Antes, a fila do assistente ficava parada 15 min esperando um login manual.
  Ligado no assistente e no lote; o demo continua com `--login-automatico` opcional.
- **Nova tentativa na OpenAI** (`robo/gpt.py`): erro passageiro (408, 409, 429, 5xx, rede fora, timeout) tenta de novo até
  4 vezes, com espera crescente (5, 10, 20 s ou o `Retry-After` da OpenAI). Vale para fotos, FIPE, tabela e documentos.
  Erro definitivo (chave inválida, pedido errado) falha na hora, como antes.
- **Lista de trabalhos com trava** (`robo/assistente.py`): `_trabalho`, a resposta "ainda analisando" de `/api/resultado` e a
  vigia de telas liam a lista sem a trava enquanto outra analista pedia um card. Às vezes isso dava erro e a extensão
  mostrava "resposta inválida".
- **Incertezas por card** (`robo/assistente.py`): a lista é limpa no início e no fim de cada card. Antes, as incertezas de um
  card que falhou no meio entravam no registro do próximo.
- **Memória do servidor com limite**: o cache de miniaturas recomeça ao passar de 600 imagens, e a lista de custos em memória
  é limpa a cada card (o custo já foi gravado no resultado e no `custos_ia.csv`).
- **Log em arquivo**: `robo/saida/logs/assistente-AAAAMMDD.log`.
- Pedido com `Content-Length` negativo recusado (antes travava a thread lendo o corpo).
- **Servidor**: `servidor/iniciar_assistente.bat` e `servidor/instalar_servidor.ps1` (Windows; trocados pelo Ubuntu na mudança acima).
- **Extensão 0.10.0**: o ícone da extensão abre uma janelinha de conexão (servidor + chave + situação da conexão). A mesma
  tela continua em "Opções da extensão".

Testado sem CRM real: nova tentativa com respostas falsas (429 → rede fora → sucesso; 400 falha na hora), os três caminhos
do login com navegador falso, 4 threads lendo enquanto outra escreve (0 erros), card com erro não deixa incerteza para trás,
servidor numa porta de teste (403 sem cabeçalho, 413 com tamanho negativo, log gravado).
**Ainda não testado na tela de login real do PowerCRM.**
