#!/usr/bin/env bash
# Příprava serveru pro lihoviny.kubanekmartin.cz. Opakovatelná; spouští root z kopie deploy/:
#
#   scp -r deploy ubuntu@130.61.26.171:lihoviny-setup
#   ssh ubuntu@130.61.26.171 sudo bash lihoviny-setup/setup-server.sh web
#
#   web   web v hostitelském nginx a certifikát Let's Encrypt (webroot)
#
# Ostatních webů na serveru se nedotýká: přidává jen vlastní soubory a před každým reloadem
# nginx spustí `nginx -t`; když test selže, vlastní změnu vrátí.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
DOMAIN=lihoviny.kubanekmartin.cz
ACME_ROOT=/var/www/$DOMAIN/acme
NGINX_SITE=/etc/nginx/sites-available/$DOMAIN
NGINX_ENABLED=/etc/nginx/sites-enabled/$DOMAIN
CERT=/etc/letsencrypt/live/$DOMAIN/fullchain.pem

step() { printf '\n== %s\n' "$*"; }
die() {
    printf 'chyba: %s\n' "$*" >&2
    exit 1
}

# Nainstaluje konfiguraci webu ze souboru $1; když `nginx -t` selže, vrátí předchozí stav.
install_site() {
    local backup=""
    if [[ -f $NGINX_SITE ]]; then
        backup=$(mktemp)
        cp -p "$NGINX_SITE" "$backup"
    fi
    install -o root -g root -m 644 "$1" "$NGINX_SITE"
    ln -sfn "$NGINX_SITE" "$NGINX_ENABLED"
    if ! nginx -t; then
        if [[ -n $backup ]]; then
            cp -p "$backup" "$NGINX_SITE"
        else
            rm -f "$NGINX_ENABLED" "$NGINX_SITE"
        fi
        nginx -t || true
        die "nginx -t selhal, konfigurace $DOMAIN vrácena zpět; nginx nebyl reloadován"
    fi
    systemctl reload nginx
    [[ -z $backup ]] || rm -f "$backup"
}

setup_web() {
    step "nginx: $DOMAIN"
    install -d -o root -g root -m 755 "$ACME_ROOT"
    if [[ ! -f $CERT ]]; then
        step "certifikát: zatím jen HTTP kvůli ověření certbotem"
        local bootstrap
        bootstrap=$(mktemp)
        cat >"$bootstrap" <<EOF
# Dočasné: jen ověření certbotem, než existuje certifikát (deploy/setup-server.sh).
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    server_tokens off;
    location ^~ /.well-known/acme-challenge/ {
        root $ACME_ROOT;
        try_files \$uri =404;
    }
    location / {
        return 404;
    }
}
EOF
        install_site "$bootstrap"
        rm -f "$bootstrap"
        # Účet u Let's Encrypt už na serveru je (ostatní weby); obnovu dělá certbot.timer.
        certbot certonly --webroot -w "$ACME_ROOT" -d "$DOMAIN" \
            --non-interactive --key-type ecdsa --deploy-hook "systemctl reload nginx"
    fi
    step "nginx: plná konfigurace s TLS"
    install_site "$here/nginx/$DOMAIN.conf"
}

[[ $EUID -eq 0 ]] || die "spusť jako root (sudo)"
case "${1:-}" in
    web) setup_web ;;
    *) die "použití: setup-server.sh web" ;;
esac
