#!/usr/bin/env bash
# Installa il bot intraday come servizio sul Raspberry (accanto a Butter, senza toccarlo).
# Uso, sul Raspberry:   bash ~/segnalatore-trading/raspberry/installa.sh
set -euo pipefail

CARTELLA="$(cd "$(dirname "$0")/.." && pwd)"
UTENTE="$(whoami)"
ENV=/etc/bot-trading.env

echo "== Pacchetti (solo git e requests, niente pandas) =="
sudo apt-get update -qq
sudo apt-get install -y -qq git python3 python3-requests

echo "== Ora di sistema sincronizzata (serve per gli orari di borsa) =="
sudo timedatectl set-ntp true || true
timedatectl | grep -E "Local time|synchronized" || true

if [ ! -f "$ENV" ]; then
  echo "== Creo $ENV: inserisci le chiavi =="
  sudo install -m 600 -o "$UTENTE" /dev/null "$ENV"
  cat > "$ENV" <<'EOF'
# Chiavi del conto PAPER di Alpaca (iniziano con PK)
ALPACA_KEY=
ALPACA_SECRET=
# Codice push dell'app (dalla schermata Configurazione notifiche)
EXPO_PUSH_TOKEN=
# 1 = carica stato.json su GitHub per l'app (serve l'accesso git, vedi README)
GIT_PUSH=1
EOF
  echo ">>> Apri il file e inserisci le chiavi:  nano $ENV"
fi

echo "== Servizio systemd =="
sudo tee /etc/systemd/system/bot-trading.service >/dev/null <<EOF
[Unit]
Description=Bot trading intraday (conto paper Alpaca)
After=network-online.target time-sync.target
Wants=network-online.target

[Service]
User=$UTENTE
WorkingDirectory=$CARTELLA
EnvironmentFile=$ENV
ExecStart=/usr/bin/python3 -u $CARTELLA/bot_intraday.py
Restart=always
RestartSec=30
# limiti per non disturbare Butter
Nice=5
MemoryMax=200M

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable bot-trading

if grep -q "^ALPACA_KEY=PK" "$ENV"; then
  echo "== Prova a secco (nessun ordine) =="
  set -a; . "$ENV"; set +a
  PROVA=1 python3 "$CARTELLA/bot_intraday.py" | tail -5
  sudo systemctl restart bot-trading
  echo "Avviato. Log in diretta:  journalctl -u bot-trading -f"
else
  echo "Chiavi mancanti: compila $ENV e poi lancia:  sudo systemctl start bot-trading"
fi
