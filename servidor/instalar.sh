#!/usr/bin/env bash
# Instala o assistente como serviço num Ubuntu sem tela. Rodar UMA vez, da raiz do projeto, com o usuário que vai
# rodar o serviço (não root):   bash servidor/instalar.sh
# Depois de atualizar o código: bash servidor/instalar.sh de novo (refaz dependências e reinicia o serviço).
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USUARIO="$(id -un)"
PORTA=8780
cd "$RAIZ"

if [ "$USUARIO" = "root" ]; then
  echo "Rode com o usuário do serviço (não root); o script pede sudo quando precisa." >&2
  exit 1
fi
if [ ! -f .env ]; then
  echo "Falta o .env na raiz ($RAIZ/.env): copie do PC de desenvolvimento." >&2
  exit 1
fi
grep -qiE '^[[:space:]]*login[[:space:]]*=' .env && grep -qiE '^[[:space:]]*password[[:space:]]*=' .env || {
  echo "O .env precisa de login= e password= (login automático do PowerCRM: o servidor não tem tela)." >&2
  exit 1
}

echo "== pacotes do sistema"
sudo apt-get update -qq
# libgl1/libglib2.0-0: o OpenCV que o RapidOCR puxa precisa delas mesmo sem tela
sudo apt-get install -y -qq libgl1 libglib2.0-0

# Python 3.12 ou mais novo (o código e as bibliotecas fixadas não rodam no 3.10 do Ubuntu 22.04)
PY=""
for c in python3.13 python3.12 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Precisa de Python 3.12+. No Ubuntu 24.04 já vem. No 22.04:" >&2
  echo "  sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt-get install -y python3.12 python3.12-venv" >&2
  exit 1
fi
sudo apt-get install -y -qq "$(basename "$PY")-venv" 2>/dev/null || sudo apt-get install -y -qq python3-venv

echo "== ambiente Python (.venv, $("$PY" --version))"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r robo/requirements.txt
.venv/bin/pip uninstall -y -q opencv-python 2>/dev/null || true   # o RapidOCR puxa a versão com tela; fica só a headless
.venv/bin/pip install -q --force-reinstall --no-deps opencv-python-headless==5.0.0.93

echo "== Chromium do Playwright (+ bibliotecas do sistema)"
sudo .venv/bin/python -m playwright install-deps chromium
.venv/bin/python -m playwright install chromium

echo "== permissões"
chmod 600 .env
mkdir -p robo/saida/logs robo/.sessao
chmod 700 robo/.sessao

echo "== serviço systemd (assistente-powercrm)"
sudo tee /etc/systemd/system/assistente-powercrm.service >/dev/null <<UNIDADE
[Unit]
Description=Assistente de conferência PowerCRM (só leitura no CRM)
After=network-online.target
Wants=network-online.target

[Service]
User=$USUARIO
WorkingDirectory=$RAIZ
Environment=PYTHONIOENCODING=utf-8
Environment=PYTHONUNBUFFERED=1
Environment=ROBO_SEM_TELA=1
ExecStart=$RAIZ/.venv/bin/python -m robo.assistente --rede --sem-navegador --porta $PORTA
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIDADE
sudo systemctl daemon-reload
sudo systemctl enable --now assistente-powercrm
sudo systemctl restart assistente-powercrm

if command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active"; then
  echo "== firewall: porta $PORTA só para a rede interna"
  for rede in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do
    sudo ufw allow from "$rede" to any port "$PORTA" proto tcp >/dev/null
  done
fi

sleep 3
echo
systemctl --no-pager --lines=5 status assistente-powercrm || true
echo
echo "Pronto. Endereço para a extensão: http://$(hostname -I | awk '{print $1}'):$PORTA"
echo "Administração:      http://$(hostname -I | awk '{print $1}'):$PORTA/admin   (senha: admin_senha= no .env)"
grep -qiE '^[[:space:]]*admin_senha[[:space:]]*=' .env || echo "   ATENÇÃO: falta admin_senha= no .env: a administração fica desligada até você pôr (não precisa reiniciar)"
echo "Log:                tail -f robo/saida/logs/assistente-\$(date +%Y%m%d).log   (ou journalctl -u assistente-powercrm -f)"
