#!/usr/bin/env bash
# Listing Radar — jednorazowa instalacja na świeżym Ubuntu (Oracle Cloud).
# Uruchomienie:  cd ~/listing-radar && chmod +x setup_vm.sh && ./setup_vm.sh
set -euo pipefail

cd "$HOME/listing-radar"

echo "======================================================"
echo " Listing Radar — instalacja na serwerze"
echo "======================================================"

echo
echo "[1/5] Instaluję Pythona..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-full || \
  sudo apt-get install -y python3 python3-venv

echo
echo "[2/5] Środowisko wirtualne + zależności..."
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

echo
echo "[3/5] Test Telegrama (przyjdzie wiadomość testowa)..."
if .venv/bin/python test_tg.py; then
  echo "Telegram: OK"
else
  echo "UWAGA: test TG nie przeszedł — sprawdź config.json."
  echo "Usługa i tak wystartuje; możesz powtórzyć: .venv/bin/python test_tg.py"
fi

echo
echo "[4/5] Usługa autostartu (systemd)..."
sudo cp listing-radar.service /etc/systemd/system/listing-radar.service
sudo systemctl daemon-reload
sudo systemctl enable listing-radar >/dev/null 2>&1
sudo systemctl restart listing-radar

echo
echo "[5/5] Czekam na start i sprawdzam..."
sleep 4
if systemctl is-active --quiet listing-radar; then
  echo "✅ Usługa ACTIVE (running)"
else
  echo "⚠ Usługa nie wystartowała — ostatnie logi:"
  sudo journalctl -u listing-radar -n 30 --no-pager || true
  exit 1
fi

echo
echo "----- ostatnie logi -----"
sudo journalctl -u listing-radar -n 15 --no-pager
echo "-------------------------"
echo
echo "🎉 GOTOWE! Radar działa24/7 na serwerze."
echo "   Logi na żywo:   sudo journalctl -u listing-radar -f"
echo "   Status:         sudo systemctl status listing-radar"
echo "   Lokalnego watcha na komputerze możesz ZATRZYMAĆ (Ctrl+C)."
