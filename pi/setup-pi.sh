#!/usr/bin/env bash
# Pi üzerinde çalıştır: sudo bash setup-pi.sh <URL> <TOKEN>
# İsteğe bağlı (sunucu çökerse Telegram'dan doğrudan uyarı için): aynı klasörde `extra.env`
# dosyası oluştur; içine NOBETCI_TG_TOKEN=... ve NOBETCI_TG_CHAT=... yaz.
set -euo pipefail
URL="${1:?kullanım: setup-pi.sh <URL> <TOKEN>}"
TOKEN="${2:?kullanım: setup-pi.sh <URL> <TOKEN>}"
DIR="$(cd "$(dirname "$0")" && pwd)"

install -d /opt/nobetci
install -m 755 "$DIR/heartbeat.py" /opt/nobetci/heartbeat.py
umask 077
{
  echo "NOBETCI_URL=$URL"
  echo "NOBETCI_TOKEN=$TOKEN"
  if [ -f "$DIR/extra.env" ]; then cat "$DIR/extra.env"; fi
} > /etc/nobetci.env
umask 022
install -m 644 "$DIR/nobetci.service" /etc/systemd/system/nobetci.service
install -d /etc/systemd/system.conf.d
install -m 644 "$DIR/watchdog.conf" /etc/systemd/system.conf.d/nobetci-watchdog.conf
systemctl daemon-reload
systemctl daemon-reexec
systemctl enable nobetci
systemctl restart nobetci
systemctl --no-pager status nobetci | head -n 8
