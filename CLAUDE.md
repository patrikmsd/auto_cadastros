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
3. Administração: `http://IP_DO_SERVIDOR:8780/admin`, com a senha `admin_senha=` do `.env` (sem ela a página fica
   desligada). Lá se cria e revoga a chave de cada analista, se vê a fila, a sessão do CRM, o custo de IA do dia, os erros,
   o log e as **observações dos analistas**. Reserva pela linha de comando: `--nova-chave "Nome"` / `--chaves`.
   As chaves ficam em `robo/saida/assistente/analistas.json`.
4. Extensão em cada PC: `chrome://extensions` → Modo do desenvolvedor → Carregar sem compactação → pasta `extensao/`.
   O ícone da extensão abre a janelinha de conexão: endereço do servidor (ex.: `http://192.168.0.50:8780`) e a chave.

Comandos: `sudo systemctl status|restart|stop assistente-powercrm` · `journalctl -u assistente-powercrm -f`.
No PC de desenvolvimento (Windows) o mesmo código roda com tela; `ROBO_SEM_TELA=1` força o modo do servidor.

Logs: `robo/saida/logs/assistente-AAAAMMDD.log` (tudo o que sai no terminal, com hora). Custos de IA: `robo/saida/custos_ia.csv`.
Incertezas: `robo/saida/incertezas.csv`. Observações dos analistas: banco SQLite `robo/saida/assistente/assistente.db`
(tabela `observacoes`; o `observacoes.csv` continua como cópia). A porta 8780 é HTTP sem criptografia: **nunca abrir para a internet**.

## Regras que não podem quebrar

- **Só leitura no CRM.** `robo/sessao.py` cancela todo POST/PUT/PATCH/DELETE fora da lista `LEITURA_PERMITIDA` (o PowerCRM
  usa POST até para ler). Gravação só no beta (`ROBO_MODO=teste`) ou no card de teste durante o clique em Salvar.
- Toda chamada de IA registra o custo (`robo/custo_ia.py`); modelo novo precisa entrar em `PRECOS`.
- Toda leitura sem certeza vai para `incertezas.anotar(...)`.
- FIPE: a do mês em que a cotação foi feita.
- Antes de mover ou refatorar estrutura, explicar em linguagem simples e esperar aprovação.

## Configuração (`.env` na raiz, fora do Git)

`powercrm`, `login`, `password` (login automático do robô), `api_gpt`, `api_mistral`, `apigoogle`, `anthropic_api_key`,
`admin_senha` (página /admin), opcionais `empresa`, `gpt_modelo`, `fipe_modelo`, `dolar`, `robo_modo`.

Dados que mudam e precisam ser atualizados à mão: `robo/regras/tabela_aceitacao_22-05-2026.txt`, `NOMES_ANALISTAS` em
`robo/config.py` (analista nova sem nome ali → o robô abre os cards dela), as planilhas da raiz e o preço do `gpt-6-luna`
em `robo/custo_ia.py` (marcado como não confirmado: conferir na fatura).

## Histórico de mudanças

### 2026-11-24 · card criado há mais de um mês dava "nenhum card encontrado"

- O `8DJZV8MbDd` (JOG4B51, tag BARBARA, em "Vendas concretizadas") foi pedido 8 vezes e sempre deu "nenhum card
  encontrado": sem data na URL, a busca do funil usa o padrão do CRM (cards criados no último mês) e o card era de 21/10.
- `buscar_card` (`robo/explorar.py`) agora abre o funil com `dateBegin=FILTRO_DESDE` até amanhã, sem filtro de tag ou
  vistoria; quem decide se o card é aberto continua sendo a tag da analista. Testado no PowerCRM real (só leitura) com
  `8DJZV8MbDd` e `lodMnL1KDV`. Precisa reiniciar o serviço.

### 2026-11-23 · disco cheio derrubava o navegador; página "Análises" no /admin

- Das 13:23 às 14:35 todo card dava "Page crashed"/"Target crashed" (403 erros no índice): o disco do servidor estava
  100% cheio e o Chromium caía por falta de espaço temporário ("ERR_INSUFFICIENT_RESOURCES"). Na mesma hora a leitura do
  filtro devolveu 11.693 cards (normal: ~130). A infra tinha aumentado o disco virtual para 300 GB, mas a partição
  continuava com 99 GB; depois do `growpart` + `resize2fs` o filtro voltou a 132 cards e o `X208bNxL2x` foi analisado.
  Ainda não se sabe o que ocupa os 92 GB (o robô usa menos de 2 GB).
- `/admin#analises`: página separada com o histórico de todas as análises (pedidas e automáticas; filtro por texto,
  situação e origem; totais e custo) e a tabela "Cards do filtro", que saiu da página principal. Rota nova
  `/api/admin/analises` (`analises_admin`), buscada só com a página aberta. Testado com servidor de teste e os dados
  reais do índice; precisa reiniciar o serviço para a rota nova valer.

### 2026-11-19 · card sem placa (zero km) é analisado

- O card `G2L8BYJ02w` (Saveiro 2027 sem placa) dava "nenhum card encontrado": a busca só reconhecia o card pela linha
  "PLACA - veículo". `JS_BUSCA` (`robo/explorar.py`) agora lê cada `section.pwrcrm-card` (código no `id`, veículo em
  `.vehicle-title`) e devolve `codigo`; `ler_card` clica pelo código. Sem placa, pasta do card e das fotos usam o código
  (`explorar.identificador`). Análise automática não exige mais placa. Card sem placa: CRLV/nota não é descartado por
  "outra placa" e a IA dos documentos é avisada de que placa vazia não é divergência. O lote continua pulando sem placa.
- FIPE: `fipe._norm` ignora espaços sobrando (a FIPE devolve "Saveiro Robust 1.6 Total Flex 16V " com espaço no fim, que
  dava "nome no card ≠ FIPE" DIVERGENTE).
- Testado no PowerCRM real (só leitura) com o `G2L8BYJ02w`: achado, aberto, 33 fotos, resultado montado.

### 2026-11-18 · FIPE igual ao card = confere

- `fipe_igual_confere(res)`, aplicado ao entregar o resultado (vale para os já gravados): Código FIPE igual ao do card,
  Valor FIPE batendo e dúvida da IA **só de versão** ("IA sem certeza") → "confere"; a dúvida fica na nota do item.
  IA × regras discordando do **modelo** (ex.: XTZ Lander × FZ25 Fazer) continua "conferir".
- Observações "que não chegavam": iam para o servidor antigo deste PC (127.0.0.1:8780, 7 observações no banco local),
  não para o Ubuntu. A extensão precisa apontar para o endereço do servidor.

### 2026-11-18 · endereço do comprovante sugerido (extensão 0.14.0)

- Endereço (CEP, logradouro, número, bairro): "...", "-", "0", "S/N" no card contam como vazio → vira **sugestão** com
  "preencher" (`ENFEITE` em `robo/assistente.py`). Card com endereço diferente do comprovante → "conferir" com o botão
  **usar o do documento** (campo `pode_trocar`; troca só na tela, o analista salva). Número: "00115" = "115".
- Testado refazendo 4 resultados gravados (casos "...", "S/N", "Rua Alpha6" × "RUA ALPHA", "00115").
- Diagnóstico da digitação: o Chrome do analista ainda rodava a 0.12 (a 0.13 não tinha sido recarregada). Depois de
  atualizar a pasta, é preciso clicar em ↻ em chrome://extensions.

### 2026-11-19 · só cards da BARBARA
- `config.ANALISTAS_LIBERADAS = {"BARBARA"}` fixo no código (o `.env` `analistas_liberadas` não é mais lido).
  `card_liberado` exige tag de analista e todas da lista: card **sem tag** ou com outra analista agora é pulado,
  no automático e no pedido manual ("card não está com a tag BARBARA").

### 2026-11-18 · cards da BARBARA também são analisados

- `config.ANALISTAS_LIBERADAS` (`.env`: `analistas_liberadas=BARBARA`, separado por vírgula; vazio = nenhuma; padrão
  BARBARA): card com tag **só** de analistas dessa lista é analisado pelo assistente (automático e pedido). Tag de
  qualquer outra analista (ou BARBARA + outra) continua pulada. Demo e lote não mudam.
- "pulado" não segura mais o card: quando uma analista é liberada, os cards dela entram na próxima leitura.
- Lista real nesse dia: 94 no filtro, 1 sem tag + 18 só BARBARA entram; 75 de outras analistas ficam de fora.

### 2026-11-18 · digitação no card real e FIPE "igual ao card" (extensão 0.13.0)

- Testado no PowerCRM real (Playwright, só leitura): o CRM usa jQuery 1.12.4 + Bootstrap 3.3.7, cujo enforceFocus é um
  listener de `focus` em captura no document; barrar no host não bastava (só entravam letras acentuadas). Agora o
  painel barra `focus`/`focusin` do próprio host já na captura da window. Depois: texto inteiro, foco na caixa, envio ok.
- "conferir" tem cor própria (laranja) e seção "Para conferir", separada de "Divergências" (vermelho). Antes as duas eram
  vermelhas na mesma seção e a FIPE com dúvida só de versão parecia divergente.
- Código FIPE igual ao do card: fonte "· igual ao card" e a nota explica que a dúvida é só a versão.

### 2026-11-18 · caixa "o que está errado" fixa no painel (extensão 0.12.0)

- O card do PowerCRM é modal Bootstrap com enforceFocus: o foco na observação era puxado de volta para o modal e não
  dava para digitar. O host do painel agora para focusin/focusout/teclas (menos Esc fora da caixa de texto).
- Redesenhar o painel não volta mais ao topo: guarda rolagem, foco/cursor e os `details` abertos.
- Caixa de retorno fixa no rodapé do painel (sem rolar): cada item marcado "está errado" entra na hora como etiqueta
  (IA sugeriu X; × tira); texto opcional; "Enviar (N itens)". Falhou → avisa o motivo e mantém o texto e as marcações.
- Campo "seu nome" removido: o nome vem da chave do analista no servidor.
- Testado numa página que imita o card (com enforceFocus): 0 roubos de foco, envio com o item vinculado, rolagem mantida.

### 2026-11-18 · filtro do sistema desde 01/01/2020

- `config.url_filtro_semana()` (usado pela análise automática, pelo lote e pela leitura do card) agora busca cards
  **criados desde 01/01/2020** até hoje, não mais só desde a segunda-feira da semana. Muda com `filtro_desde=` no `.env`
  (`2020-01-01` ou `01/01/2020`). Tags e vistoria continuam iguais. O lote ainda aceita `--inicio`.
- Contagem real nesse dia: 99 cards no filtro (74 Retornada, 25 Em aprovação), 3 com placa e sem tag de analista.

### 2026-11-18 · tabela de preço relida quando o PDF muda + README

- `robo/precos.py`: `tabelas_preco.json` guarda o nome, o tamanho e a data de cada PDF de preço; se algum mudar, os PDFs
  são lidos de novo sozinhos. Antes era preciso apagar o cache à mão. Testado: 1ª leitura 4 s, depois cache, PDF com
  data alterada → relido.
- Os PDFs "VALLE TABELA PREÇO" têm o conteúdo da ANCORE: o nome não muda a conta, só aparece como fonte no resultado.
- `README.md`: como testar no próprio PC (Windows) e como instalar no servidor.

### 2026-11-18 · análise automática dos cards do filtro

- Quando a fila dos analistas está vazia, o trabalhador do `robo/assistente.py` lê a lista do filtro das analistas
  (`lote.listar`, o mesmo filtro do lote; só lê o funil, não põe tag), analisa um card novo por vez (com placa e sem
  tag de nome de analista) e, ao acabar, lê a lista de novo; sem card novo, espera 2 min; sem conseguir ler a lista, 5 min.
- Pedido de analista passa sempre na frente (a fila dele é olhada antes de cada card automático).
- Card que deu erro é tentado no máximo 2 vezes; já analisado ou pulado não é refeito.
- **Card com tag de nome de analista não é analisado** (automático nem manual: fica "pulado"). Chegou a ser liberado no
  assistente (commit f1b2877) e voltou atrás no mesmo dia, a pedido.
- Selo em cima de cada card no funil (extensão 0.11.0): `section.pwrcrm-card` tem o código do card no `id`; a extensão
  pergunta `/api/situacoes?c=...` a cada 8 s e mostra "✓ Analisado 14:32 · conferir · 3 sugestões", "Robô analisando
  agora", "Na fila do robô" ou "deu erro". Card sem análise fica sem selo; servidor fora do ar → sem selo.
- Liga/desliga na tela /admin (guardado em `robo/saida/assistente/auto.json`; padrão ligado, `auto=0` no `.env` muda o padrão).
- Testado: ciclo com CRM simulado (ordem, prioridade, card novo na releitura, erro com 1 nova tentativa, desligado não
  roda) e leitura real da lista no PowerCRM sem tela (7 cards em 7 s, nenhum analisado).

### 2026-11-18 · página de administração e banco das observações

- `robo/admin.html` + rotas `/admin` e `/api/admin/*` em `robo/assistente.py`: senha no cabeçalho `X-Admin` (comparação em
  tempo constante; senha errada espera 1 s). Analistas (criar/mostrar/copiar/revogar chave), situação (rodando, fila,
  prontos hoje, custo de IA do dia, sessão do CRM, fotos de falha de login), observações (com filtro), erros e log.
- Observações dos analistas agora vão para o SQLite `robo/saida/assistente/assistente.db`; na primeira vez o que estava
  no `observacoes.csv` é trazido para o banco. O CSV continua sendo gravado.
- Rede virou o padrão (`--local` para atender só o próprio PC); `--rede` ainda é aceito.
- Testado com servidor de teste: senha errada/ausente → 403, sem `admin_senha` → 503, criar e revogar chave, observação
  gravada e lida do banco, caminho de foto inválido → 404. Página ainda não aberta num navegador do servidor real.

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
