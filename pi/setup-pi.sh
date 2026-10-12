#!/usr/bin/env bash
# Pi üzerinde çalıştır: sudo bash setup-pi.sh <URL> <TOKEN>
set -euo pipefail
URL="${1:?kullanım: setup-pi.sh <URL> <TOKEN>}"
TOKEN="${2:?kullanım: setup-pi.sh <URL> <TOKEN>}"
DIR="$(cd "$(dirname "$0")" && pwd)"

install -d /opt/nobetci
install -m 755 "$DIR/heartbeat.py" /opt/nobetci/heartbeat.py
umask 077
cat > /etc/nobetci.env <<EOF
NOBETCI_URL=$URL
NOBETCI_TOKEN=$TOKEN
EOF
install -m 644 "$DIR/nobetci.service" /etc/systemd/system/nobetci.service
systemctl daemon-reload
systemctl enable --now nobetci
systemctl --no-pager status nobetci | head -n 8
