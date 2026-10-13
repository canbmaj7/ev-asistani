#!/usr/bin/env bash
# Pi ajanı için yeni sürüm çıkarır: AGENT_VERSION'ı artırır, pi/heartbeat.py'yi gizli anahtarla imzalar
# (pi/heartbeat.py.sig). Pi yalnızca bu imzayı doğrulayabildiği dosyayı çalıştırır.
#
#   scripts/release-agent.sh          # sürümü 1 artır + imzala
#   scripts/release-agent.sh --sign   # sürümü artırmadan yalnızca (yeniden) imzala
#
# Gizli anahtar: $RELEASE_KEY (varsayılan ~/.ssh/evpi_release_ed25519.pem). Repoya ASLA girmez.
set -euo pipefail
cd "$(dirname "$0")/.."
KEY="${RELEASE_KEY:-$HOME/.ssh/evpi_release_ed25519.pem}"
AGENT=pi/heartbeat.py
[ -r "$KEY" ] || { echo "gizli anahtar bulunamadı: $KEY" >&2; exit 1; }

if [ "${1:-}" != "--sign" ]; then
  cur=$(grep -E '^AGENT_VERSION *= *[0-9]+' "$AGENT" | head -1 | grep -oE '[0-9]+' | head -1)
  next=$((cur + 1))
  sed -i -E "s/^AGENT_VERSION = [0-9]+/AGENT_VERSION = $next/" "$AGENT"
  echo "sürüm: $cur → $next"
fi

openssl pkeyutl -sign -inkey "$KEY" -rawin -in "$AGENT" | base64 -w0 > "$AGENT.sig"
# Doğrula: imza genel anahtarla tutuyor mu?
openssl pkeyutl -verify -pubin -inkey pi/release.pub -rawin -in "$AGENT" -sigfile <(base64 -d "$AGENT.sig") >/dev/null
echo "imzalandı ve doğrulandı: $AGENT.sig (sürüm $(grep -E '^AGENT_VERSION' "$AGENT" | grep -oE '[0-9]+' | head -1))"
echo "Sonraki adım: git add pi && git commit && git push, sonra Telegram'da /pi-guncelle"
