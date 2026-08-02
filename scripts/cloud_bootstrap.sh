#!/usr/bin/env bash
# One-shot environment setup for a fresh 24/7 host (Oracle Cloud free tier, etc).
#
# On the VM, run ONE of these:
#   curl -fsSL https://raw.githubusercontent.com/fun0963/p1_quantfinance/main/scripts/cloud_bootstrap.sh | bash
#   ./scripts/cloud_bootstrap.sh          # if you already cloned the repo
#
# What it does: docker + compose, docker enabled at boot (so `restart:
# unless-stopped` survives reboots), swap on small instances, clone the repo,
# and write a .env TEMPLATE. It deliberately does NOT touch your API keys and
# does NOT start trading - you fill .env, then run scripts/cloud_verify.sh.
#
# Safe to re-run: every step is idempotent and an existing .env is never
# overwritten.
set -euo pipefail

REPO_URL="https://github.com/fun0963/p1_quantfinance.git"
REPO_DIR="${HOME}/p1_quantfinance"

say() { printf '\n=== %s ===\n' "$1"; }

say "1/5 packages (docker, compose, git)"
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker.io docker-compose-v2 git
elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y -q docker git
    # Oracle Linux ships compose as a plugin package under a different name.
    sudo dnf install -y -q docker-compose-plugin || true
else
    echo "unsupported distro: need apt-get or dnf" >&2
    exit 1
fi

say "2/5 docker enabled at boot"
sudo systemctl enable --now docker
if ! id -nG "$USER" | grep -qw docker; then
    sudo usermod -aG docker "$USER"
    echo "NOTE: added $USER to the docker group - log out and back in (or run"
    echo "      'newgrp docker') before the docker commands work without sudo."
fi

say "3/5 swap (only on small instances)"
mem_mb=$(free -m | awk '/^Mem:/{print $2}')
if [ "$mem_mb" -lt 2048 ] && [ ! -f /swapfile ]; then
    echo "RAM ${mem_mb}MB -> adding 2G swap"
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    sudo swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
else
    echo "RAM ${mem_mb}MB -> no swap needed (or /swapfile already present)"
fi

say "4/5 repository"
if [ -d "${REPO_DIR}/.git" ]; then
    git -C "$REPO_DIR" pull --ff-only
else
    git clone "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"

say "5/5 .env template"
if [ -f .env ]; then
    echo ".env already exists - left untouched"
else
    cat > .env <<'ENVEOF'
# Fill in the two Alpaca PAPER keys, then save. Nothing else needs changing.
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_PAPER=true

# Single-writer guard: must match the container hostname in
# docker-compose.live.yml. Do NOT change unless you change both.
EXECUTE_HOST=quant-live

# Optional but strongly recommended - this is how you find out something broke.
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
ALERTS_ENABLED=true
ENVEOF
    chmod 600 .env
    echo "wrote .env template (mode 600)"
fi

cat <<'NEXT'

--------------------------------------------------------------------
DONE. Two things left, both yours to do:

  1. Put your Alpaca PAPER keys in the .env file:
         nano ~/p1_quantfinance/.env
     (Telegram token/chat id too, if you want alerts on your phone.)

  2. Start and verify:
         cd ~/p1_quantfinance && ./scripts/cloud_verify.sh

Optional, from your workstation - carry over TCA samples and history:
     scp -r <local>/p1_quantfinance/data <user>@<VM_IP>:~/p1_quantfinance/
--------------------------------------------------------------------
NEXT
