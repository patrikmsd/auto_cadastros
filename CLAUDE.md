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

Roda num servidor local da rede, numa sessão do Windows com tela: se o login automático do CRM falhar, a janela do
navegador precisa aparecer para alguém terminar o login.

1. Instalar uma vez, no PowerShell como Administrador: `powershell -ExecutionPolicy Bypass -File servidor\instalar_servidor.ps1`
   (cria a tarefa "Assistente PowerCRM", que liga o assistente quando o usuário entra no Windows, libera a porta 8780 no
   Firewall só para rede privada/domínio e desliga a suspensão na tomada).
2. `servidor\iniciar_assistente.bat` liga `robo.assistente --rede --sem-navegador` e religa o assistente 10 s depois se ele cair
   (os reinícios ficam em `robo/saida/logs/reinicios.log`).
3. Chave de cada analista: `python -m robo.assistente --nova-chave "Nome"` (lista: `--chaves`). Fica em `robo/saida/assistente/analistas.json`.
4. Extensão em cada PC: `chrome://extensions` → Modo do desenvolvedor → Carregar sem compactação → pasta `extensao/`.
   O ícone da extensão abre a janelinha de conexão: endereço do servidor (ex.: `http://192.168.0.50:8780`) e a chave.

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
- **Servidor**: `servidor/iniciar_assistente.bat` (liga e religa) e `servidor/instalar_servidor.ps1` (tarefa no Windows, Firewall, sem suspensão).
- **Extensão 0.10.0**: o ícone da extensão abre uma janelinha de conexão (servidor + chave + situação da conexão). A mesma
  tela continua em "Opções da extensão".

Testado sem CRM real: nova tentativa com respostas falsas (429 → rede fora → sucesso; 400 falha na hora), os três caminhos
do login com navegador falso, 4 threads lendo enquanto outra escreve (0 erros), card com erro não deixa incerteza para trás,
servidor numa porta de teste (403 sem cabeçalho, 413 com tamanho negativo, log gravado).
**Ainda não testado na tela de login real do PowerCRM.**
