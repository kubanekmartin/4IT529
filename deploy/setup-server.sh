#!/usr/bin/env bash
# Příprava serveru pro lihoviny.kubanekmartin.cz. Opakovatelná; spouští root z kopie deploy/:
#
#   scp -r deploy ubuntu@130.61.26.171:lihoviny-setup
#   ssh ubuntu@130.61.26.171 sudo bash lihoviny-setup/setup-server.sh web
#
#   web               web v hostitelském nginx a certifikát Let's Encrypt (webroot)
#   app [klic.pub]    uživatel lihoviny-deploy, skripty, /srv/lihoviny s .env (nová hesla
#                     databáze, jen když .env ještě není) a SSH klíč pro GitHub Actions, který
#                     smí spustit jen nasazovací skript
#
# Ostatních webů ani jejich uživatelů (např. `deploy` pro kubanekmartin.cz) se nedotýká:
# přidává jen vlastní soubory a před každým reloadem nginx spustí `nginx -t`; když test selže,
# vlastní změnu vrátí.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
DEPLOY_USER=lihoviny-deploy
APP_DIR=/srv/lihoviny
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

setup_app() {
    local pubkey_file=${1:-}
    step "uživatel $DEPLOY_USER (jen nasazování, skupina docker)"
    if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
        useradd --system --create-home --shell /bin/bash --user-group "$DEPLOY_USER"
    fi
    usermod -aG docker "$DEPLOY_USER"

    step "skripty"
    install -o root -g root -m 755 "$here/lihoviny-deploy" /usr/local/bin/lihoviny-deploy
    install -o root -g root -m 755 "$here/lihoviny-set-api-key" /usr/local/sbin/lihoviny-set-api-key

    step "adresář aplikace $APP_DIR"
    install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 750 "$APP_DIR" "$APP_DIR/releases"
    if [[ ! -f $APP_DIR/.env ]]; then
        # Hesla generuje openssl přímo do souboru (printf je vestavěný, nejsou v argv).
        (
            umask 077
            {
                printf '# Vytvořil deploy/setup-server.sh %s. Práva 600, nikdy do repa ani chatu.\n' \
                    "$(date -I)"
                printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 24)"
                printf 'WEB_DB_PASSWORD=%s\n' "$(openssl rand -hex 24)"
                printf '# Vkládá `sudo lihoviny-set-api-key` (čte klíč skrytě z terminálu).\n'
                printf 'ANTHROPIC_API_KEY=\n'
            } >"$APP_DIR/.env"
        )
        echo "vytvořen $APP_DIR/.env s novými hesly databáze; ANTHROPIC_API_KEY zatím prázdný"
    fi
    chown "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"

    if [[ -n $pubkey_file ]]; then
        step "SSH klíč: smí spustit jen /usr/local/bin/lihoviny-deploy"
        local pubkey tmp
        pubkey=$(<"$pubkey_file")
        [[ $pubkey =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+(\ [A-Za-z0-9@._\ -]*)?$ ]] ||
            die "čekám jeden veřejný klíč ssh-ed25519"
        # Adresář i soubor patří rootovi: uživatel si omezení klíče nemůže sám změnit.
        install -d -o root -g root -m 755 "/home/$DEPLOY_USER/.ssh"
        tmp=$(mktemp)
        printf 'restrict,command="/usr/local/bin/lihoviny-deploy" %s\n' "$pubkey" >"$tmp"
        install -o root -g root -m 644 "$tmp" "/home/$DEPLOY_USER/.ssh/authorized_keys"
        rm -f "$tmp"
    fi
}

[[ $EUID -eq 0 ]] || die "spusť jako root (sudo)"
case "${1:-}" in
    web) setup_web ;;
    app) setup_app "${2:-}" ;;
    *) die "použití: setup-server.sh web | app [klic.pub]" ;;
esac
