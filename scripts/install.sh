#!/usr/bin/env bash
set -Eeuo pipefail

DOMAIN="_"
ENABLE_TLS=0
MODEL="base"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  printf 'Usage: sudo ./scripts/install.sh [--domain api.example.com] [--tls] [--model base|small]\n'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="${2:?missing domain}"; shift 2 ;;
    --tls) ENABLE_TLS=1; shift ;;
    --model) MODEL="${2:?missing model}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  printf 'Run this installer with sudo.\n' >&2
  exit 1
fi
if [[ "$MODEL" != "base" && "$MODEL" != "small" ]]; then
  printf 'Model must be base or small.\n' >&2
  exit 2
fi
if [[ $ENABLE_TLS -eq 1 && "$DOMAIN" == "_" ]]; then
  printf -- '--tls requires --domain.\n' >&2
  exit 2
fi
if [[ "$DOMAIN" != "_" ]]; then
  if [[ ! "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || \
    [[ "$DOMAIN" == *".."* ]]; then
    printf 'Domain contains unsupported characters.\n' >&2
    exit 2
  fi
fi

export DEBIAN_FRONTEND=noninteractive
if [[ -f /etc/apt/sources.list ]]; then
  sed -Ei 's#http://[^ ]*ports\.ubuntu\.com#https://ports.ubuntu.com#g' /etc/apt/sources.list
fi
BASE_PACKAGES=(
  ca-certificates curl git rsync openssl build-essential cmake pkg-config
  ffmpeg python3 python3-venv nginx jq sqlite3
)
MISSING_PACKAGES=()
for package in "${BASE_PACKAGES[@]}"; do
  if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'ok installed'; then
    MISSING_PACKAGES+=("$package")
  fi
done
if [[ ${#MISSING_PACKAGES[@]} -gt 0 ]]; then
  apt-get -o Acquire::ForceIPv4=true -o Acquire::Retries=5 \
    -o Acquire::https::Timeout=20 update
  apt-get install -y "${MISSING_PACKAGES[@]}"
fi

if ! id linkscribe >/dev/null 2>&1; then
  useradd --system --home /opt/linkscribe --shell /usr/sbin/nologin linkscribe
fi
install -d -o linkscribe -g linkscribe /opt/linkscribe/app /var/lib/linkscribe/jobs /var/log/linkscribe
install -d -o root -g root /opt/linkscribe/bin
install -d -m 750 -o root -g linkscribe /etc/linkscribe
install -d -m 755 /var/www/certbot

if [[ ! -d /opt/whisper.cpp/.git ]]; then
  git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git /opt/whisper.cpp
else
  git -C /opt/whisper.cpp pull --ff-only
fi
cmake -S /opt/whisper.cpp -B /opt/whisper.cpp/build -DCMAKE_BUILD_TYPE=Release
cmake --build /opt/whisper.cpp/build --config Release -j"$(nproc)"
if [[ ! -f "/opt/whisper.cpp/models/ggml-${MODEL}.bin" ]]; then
  bash /opt/whisper.cpp/models/download-ggml-model.sh "$MODEL"
fi
chmod -R a+rX /opt/whisper.cpp

case "$(uname -m)" in
  aarch64|arm64)
    YTDLP_ASSET="yt-dlp_linux_aarch64"
    DENO_ASSET="deno-aarch64-unknown-linux-gnu.zip"
    ;;
  x86_64|amd64)
    YTDLP_ASSET="yt-dlp_linux"
    DENO_ASSET="deno-x86_64-unknown-linux-gnu.zip"
    ;;
  *) printf 'Unsupported CPU architecture: %s\n' "$(uname -m)" >&2; exit 1 ;;
esac

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
curl --fail --location --retry 3 \
  "https://github.com/yt-dlp/yt-dlp/releases/latest/download/$YTDLP_ASSET" \
  --output "$TMP_DIR/yt-dlp"
curl --fail --location --retry 3 \
  "https://github.com/yt-dlp/yt-dlp/releases/latest/download/SHA2-256SUMS" \
  --output "$TMP_DIR/SHA2-256SUMS"
EXPECTED_YTDLP_SHA="$(awk -v asset="$YTDLP_ASSET" '$2 == asset {print $1}' "$TMP_DIR/SHA2-256SUMS")"
printf '%s  %s\n' "$EXPECTED_YTDLP_SHA" "$TMP_DIR/yt-dlp" | sha256sum --check --status
install -m 755 "$TMP_DIR/yt-dlp" /opt/linkscribe/bin/yt-dlp

curl --fail --location --retry 3 \
  "https://github.com/denoland/deno/releases/latest/download/$DENO_ASSET" \
  --output "$TMP_DIR/deno.zip"
python3 -m zipfile -e "$TMP_DIR/deno.zip" "$TMP_DIR/deno"
install -m 755 "$TMP_DIR/deno/deno" /opt/linkscribe/bin/deno

rsync -a --delete \
  --exclude .git --exclude .venv --exclude data --exclude dist --exclude build \
  --exclude .pytest_cache --exclude .ruff_cache --exclude __pycache__ \
  --exclude docs/demo-frames \
  "$SOURCE_DIR/" /opt/linkscribe/app/
python3 -m venv /opt/linkscribe/venv
/opt/linkscribe/venv/bin/pip install --upgrade pip
/opt/linkscribe/venv/bin/pip install /opt/linkscribe/app

ENV_FILE=/etc/linkscribe/linkscribe.env
if [[ ! -f "$ENV_FILE" ]]; then
  TOKEN="$(openssl rand -hex 32)"
  cat >"$ENV_FILE" <<EOF
LINKSCRIBE_API_TOKEN=$TOKEN
LINKSCRIBE_DB_PATH=/var/lib/linkscribe/linkscribe.db
LINKSCRIBE_JOBS_DIR=/var/lib/linkscribe/jobs
LINKSCRIBE_YTDLP_BIN=/opt/linkscribe/bin/yt-dlp
LINKSCRIBE_DENO_BIN=/opt/linkscribe/bin/deno
LINKSCRIBE_WHISPER_BIN=/opt/whisper.cpp/build/bin/whisper-cli
LINKSCRIBE_WHISPER_MODEL=/opt/whisper.cpp/models/ggml-${MODEL}.bin
LINKSCRIBE_WHISPER_THREADS=$(nproc)
LINKSCRIBE_MAX_DURATION_SECONDS=7200
LINKSCRIBE_MAX_DOWNLOAD_BYTES=536870912
LINKSCRIBE_JOB_TTL_HOURS=24
LINKSCRIBE_INLINE_TRANSCRIPT_CHARS=12000
LINKSCRIBE_RATE_LIMIT_PER_MINUTE=20
LINKSCRIBE_YTDLP_COOKIES_FILE=
EOF
fi

upsert_env() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s#^${key}=.*#${key}=${value}#" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

upsert_env LINKSCRIBE_YTDLP_BIN /opt/linkscribe/bin/yt-dlp
upsert_env LINKSCRIBE_DENO_BIN /opt/linkscribe/bin/deno
upsert_env LINKSCRIBE_WHISPER_MODEL "/opt/whisper.cpp/models/ggml-${MODEL}.bin"
chown root:linkscribe "$ENV_FILE"
chmod 640 "$ENV_FILE"
chown -R linkscribe:linkscribe /opt/linkscribe/app /var/lib/linkscribe /var/log/linkscribe

install -m 644 "$SOURCE_DIR/deploy/linkscribe-api.service" /etc/systemd/system/
install -m 644 "$SOURCE_DIR/deploy/linkscribe-worker.service" /etc/systemd/system/
sed "s/__DOMAIN__/$DOMAIN/g" "$SOURCE_DIR/deploy/nginx.conf.template" > /etc/nginx/sites-available/linkscribe
ln -sfn /etc/nginx/sites-available/linkscribe /etc/nginx/sites-enabled/linkscribe
rm -f /etc/nginx/sites-enabled/default
nginx -t

systemctl daemon-reload
systemctl enable --now linkscribe-api linkscribe-worker nginx
systemctl restart linkscribe-api linkscribe-worker nginx

if [[ $ENABLE_TLS -eq 1 ]]; then
  python3 -m venv /opt/certbot
  /opt/certbot/bin/pip install --upgrade pip certbot
  /opt/certbot/bin/certbot certonly --webroot --webroot-path /var/www/certbot \
    --non-interactive --agree-tos --register-unsafely-without-email -d "$DOMAIN"
  sed "s/__DOMAIN__/$DOMAIN/g" "$SOURCE_DIR/deploy/nginx-tls.conf.template" \
    > /etc/nginx/sites-available/linkscribe
  install -m 644 "$SOURCE_DIR/deploy/linkscribe-certbot-renew.service" /etc/systemd/system/
  install -m 644 "$SOURCE_DIR/deploy/linkscribe-certbot-renew.timer" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now linkscribe-certbot-renew.timer
  nginx -t
  systemctl reload nginx
fi

curl --fail --silent http://127.0.0.1:8080/health | jq .
printf '\nLinkScribe is installed.\n'
printf 'API token: sudo sed -n "s/^LINKSCRIBE_API_TOKEN=//p" %s\n' "$ENV_FILE"
printf 'Logs: sudo journalctl -u linkscribe-api -u linkscribe-worker -f\n'
