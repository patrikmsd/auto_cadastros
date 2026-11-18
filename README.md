# Assistente de conferência de cadastro · PowerCRM

Confere os cards de cadastro no PowerCRM: lê o card, baixa as fotos e os documentos, faz OCR local, usa IA para fotos,
FIPE, tabela e documentos, e mostra o resultado **dentro do card**, por uma extensão do Chrome. **No CRM o robô só
lê.** Quem preenche e salva é o analista.

Detalhes técnicos e histórico de mudanças: [CLAUDE.md](CLAUDE.md).

---

## 1. Testar no próprio PC (Windows)

Precisa do **Python 3.12 ou mais novo** e do **Google Chrome**.

### Instalar (uma vez)

No PowerShell, na pasta do projeto:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r robo\requirements.txt
.venv\Scripts\python -m playwright install chromium
```

### Arquivo `.env`

O `.env` fica na raiz do projeto e **não vai para o Git**, porque tem senhas. Peça uma cópia a quem já usa o robô ou
crie um com estas linhas:

```
powercrm=https://app.powercrm.com.br/
login=usuario-do-robo-no-powercrm
password=senha-do-robo
empresa=ANCORE PROTEÇÃO VEICULAR
api_gpt=chave-da-openai
admin_senha=senha-da-tela-de-administracao
```

### Rodar

```powershell
$env:PYTHONIOENCODING="utf-8"
.venv\Scripts\python -m robo.assistente
```

- Abre a página do assistente em http://127.0.0.1:8780.
- A tela de administração fica em http://127.0.0.1:8780/admin e usa a senha `admin_senha` do `.env`. Por ela se
  criam as chaves dos analistas e se acompanham a fila, a análise automática, o terminal e as observações dos analistas.
- O robô entra sozinho no PowerCRM com o `login` e o `password` do `.env` e escolhe a empresa.
- Com a fila vazia, ele **analisa sozinho** os cards do filtro que ainda não têm tag de analista. O filtro pega os
  cards criados desde 01/01/2020 (dá para mudar com `filtro_desde=` no `.env`), com as tags PENDENCIAS LEVES ou
  CADASTRO COMPLETO e vistoria Em aprovação ou Retornada. A análise automática pode ser desligada na tela /admin.
- `--local` atende só este PC, sem receber conexões da rede. `Ctrl+C` encerra.

### Extensão do Chrome

1. Abra `chrome://extensions` e ligue o **Modo do desenvolvedor**, no canto de cima.
2. Clique em **Carregar sem compactação** e escolha a pasta `extensao/`.
3. Clique no ícone da extensão. Em **Servidor**, coloque `http://127.0.0.1:8780`; a chave pode ficar vazia no próprio
   PC. Clique em **Conectar**. A bolinha verde indica que está funcionando.
4. Abra o funil no PowerCRM. Os cards já analisados mostram um selo ("✓ Analisado 14:32 · …"). Ao abrir um card, o
   painel do assistente aparece ao lado.

Depois de atualizar o código (`git pull`), recarregue a extensão em `chrome://extensions` (botão ↻).

---

## 2. Servidor da empresa (Ubuntu sem tela)

```bash
git clone https://github.com/patrikmsd/auto_cadastros.git assistente
cd assistente
# copie o .env do PC de desenvolvimento para esta pasta (ele não vem no git)
bash servidor/instalar.sh
```

O instalador:
- instala as dependências e o Chromium;
- cria o serviço `assistente-powercrm`, que liga junto com a máquina e volta sozinho se cair;
- libera a porta 8780 só para a rede interna;
- no fim, mostra os endereços para a extensão e para a tela /admin.

Em cada PC de analista:
1. Carregue a extensão, como no passo 1.
2. Coloque o endereço do servidor, por exemplo `http://192.168.0.50:8780`.
3. Cole a chave criada para esse analista na tela /admin.

| Para | Comando no servidor |
|---|---|
| Atualizar | `git pull && bash servidor/instalar.sh` |
| Ver se está rodando | `sudo systemctl status assistente-powercrm` |
| Reiniciar | `sudo systemctl restart assistente-powercrm` |
| Acompanhar o log | `journalctl -u assistente-powercrm -f`, ou o terminal da tela /admin |

A porta 8780 é HTTP sem criptografia: **use só na rede interna, nunca aberta para a internet.**

---

## Arquivos de regras (na raiz)

| Arquivo | Para quê | Quando mudar |
|---|---|---|
| `BASE REGRAS ROBO CADASTRO.xlsx` | Regras de cadastro (vão para a IA e para as conferências) | Troque o arquivo, com o mesmo nome. Ele é lido a cada card |
| `VENCIMENTOS DAS ASSOCIAÇÕES*.xlsx` | Dia de vencimento liberado por associação | Troque o arquivo, mantendo o começo do nome. Ele é lido a cada card |
| `VALLE TABELA PREÇO DENTRO/FORA *.pdf` | Mensalidade por faixa de valor. O nome é "VALLE", mas o conteúdo é o da ANCORE | Troque o PDF, mantendo o começo do nome. O robô percebe e lê de novo sozinho |
| `TABELA ACEITAÇÃO ANCORE 2026 - *.pdf` | Tabela de aceitação. A IA lê o texto dela em `robo/regras/tabela_aceitacao_22-05-2026.txt` | **Manual:** refaça o `.txt` a partir do PDF novo e atualize o nome em `ACEITACAO` (`robo/decidir_tabela.py`) |
