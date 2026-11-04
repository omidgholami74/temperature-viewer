#!/usr/bin/env bash
#
# setup-server.sh — put the MQTT broker and the ingest bridge on the internal
# Linux server that already runs the PHP API.
#
#   sudo bash infrastructure/mqtt/setup-server.sh
#
# What it does, in order:
#   1. installs mosquitto, mosquitto-clients and python3-venv
#   2. creates the broker users: furnace-bridge (consumer) and one per board
#   3. writes an ACL that lets a board touch only its own topics
#   4. enables the listener from infrastructure/mqtt/furnace.conf
#   5. opens 1883 only to the board's subnet (when ufw/firewalld is active)
#   6. creates the virtual environment and installs paho-mqtt
#   7. writes /etc/furnace/mqtt-bridge.env with the passwords and the API key
#   8. installs furnace-mqtt-bridge.service and starts it
#   9. checks that the broker and the bridge are really up
#
# It is idempotent: running it again only rewrites the files and rotates the
# two passwords. Nothing else on the server is touched; the API, its database
# and the dashboard are not modified.
#
# Optional overrides (default: ask, or a sensible default):
#   sudo SITE=razi DEVICE=esp32_001 API_URL=https://apitemp.example.com/api/ingest \
#        LAN_CIDR=192.168.1.0/24 SERVICE_USER=furnace APP=/srv/furnace \
#        bash infrastructure/mqtt/setup-server.sh
#
# Passwords are read with `read -rsp`, so they never reach the shell history.
# Pass BRIDGE_PASS / DEVICE_PASS / API_KEY in the environment to skip a prompt
# (that keeps them out of history too, but they are visible in `ps` briefly).
#
#   DRY_RUN=1 bash infrastructure/mqtt/setup-server.sh    # print the plan only
#
set -euo pipefail

APP="${APP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
SERVICE_USER="${SERVICE_USER:-furnace}"
LAN_CIDR="${LAN_CIDR:-192.168.1.0/24}"
DRY_RUN="${DRY_RUN:-0}"

IDENT='^[a-z0-9][a-z0-9_-]{2,31}$'

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

say() {
    printf '\n== %s\n' "$*"
}

# Echo a command and run it, or only echo it under DRY_RUN.
run() {
    if [ "$DRY_RUN" = 1 ]; then
        printf '   + %s\n' "$*"
    else
        "$@"
    fi
}

# Consume stdin into a file, or only name it under DRY_RUN.
write_file() {
    local path="$1"

    if [ "$DRY_RUN" = 1 ]; then
        printf '   + write %s\n' "$path"
        cat > /dev/null
    else
        cat > "$path"
    fi
}

ask() {
    # ask <variable-name> <prompt> <default> [secret]
    local name="$1" prompt="$2" fallback="${3:-}" secret="${4:-0}"
    local current value=""

    current="$(eval "printf '%s' \"\${$name:-}\"")"

    if [ -n "$current" ]; then
        return 0
    fi

    if [ "$DRY_RUN" = 1 ]; then
        if [ "$secret" = 1 ]; then
            eval "$name='dry-run-password'"
        else
            eval "$name=\"\$fallback\""
        fi
        return 0
    fi

    if [ "$secret" = 1 ]; then
        read -rsp "$prompt" value
        echo
    else
        read -rp "$prompt" value
    fi

    if [ -z "$value" ]; then
        value="$fallback"
    fi

    eval "$name=\"\$value\""
}

say "1/9 preflight"

[ -f "$APP/infrastructure/mqtt/furnace.conf" ] ||
    die "not a furnace checkout (APP=$APP); pass APP=/path/to/repo"
[ -f "$APP/backend/mqtt/bridge.py" ] ||
    die "backend/mqtt/bridge.py is missing in $APP; update the checkout first"
[ -f "$APP/infrastructure/mqtt/aclfile" ] ||
    die "infrastructure/mqtt/aclfile is missing in $APP"

if [ "$DRY_RUN" != 1 ]; then
    [ "$(id -u)" = 0 ] || die "run it with sudo: it writes /etc/mosquitto and /etc/furnace"
    command -v apt-get >/dev/null 2>&1 ||
        die "this script targets Debian/Ubuntu; see docs/mqtt-setup.md for dnf"
fi

say "2/9 settings"

ask SITE "site namespace [razi]: " "razi"
ask DEVICE "board id [esp32_001]: " "esp32_001"
ask API_URL "ingest URL [https://apitemp.futurefounderco.com/api/ingest]: " \
    "https://apitemp.futurefounderco.com/api/ingest"
ask API_KEY "FURNACE_INGEST_KEY from the API config (empty when the API is open): " ""  1

[[ "$SITE" =~ $IDENT ]] || die "SITE must match $IDENT (got '$SITE')"
[[ "$DEVICE" =~ $IDENT ]] || die "DEVICE must match $IDENT (got '$DEVICE')"
[[ "$API_URL" == http*://* ]] || die "API_URL must be an http(s) URL (got '$API_URL')"

ask BRIDGE_PASS "new password for the broker user furnace-bridge: " "" 1
ask DEVICE_PASS "new password for the broker user $DEVICE: " "" 1

if [ "$DRY_RUN" != 1 ]; then
    [ -n "$BRIDGE_PASS" ] || die "the bridge password cannot be empty"
    [ -n "$DEVICE_PASS" ] || die "the board password cannot be empty"
    [ "$BRIDGE_PASS" != "$DEVICE_PASS" ] || die "use two different passwords"
fi

API_BASE="${API_URL%/api/ingest}"

printf '   site=%s device=%s api=%s\n' "$SITE" "$DEVICE" "$API_URL"
printf '   app=%s user=%s lan=%s\n' "$APP" "$SERVICE_USER" "$LAN_CIDR"

say "3/9 packages"

if [ "$DRY_RUN" = 1 ] || ! command -v mosquitto >/dev/null 2>&1; then
    run apt-get update -qq
    run apt-get install -y mosquitto mosquitto-clients python3-venv
else
    printf '   mosquitto is already installed\n'
    if ! command -v python3 >/dev/null 2>&1; then
        die "python3 is missing"
    fi
    python3 -c 'import venv' 2>/dev/null ||
        run apt-get install -y python3-venv
fi

say "4/9 broker users"

# -b alone fails on a missing file, so the first user creates it with -c.
if [ -s /etc/mosquitto/passwd ]; then
    run mosquitto_passwd -b /etc/mosquitto/passwd furnace-bridge "$BRIDGE_PASS"
else
    run mosquitto_passwd -c -b /etc/mosquitto/passwd furnace-bridge "$BRIDGE_PASS"
fi
run mosquitto_passwd -b /etc/mosquitto/passwd "$DEVICE" "$DEVICE_PASS"
run chown mosquitto:mosquitto /etc/mosquitto/passwd
run chmod 600 /etc/mosquitto/passwd

say "5/9 ACL (a board may only touch its own topics)"

sed -e "s/razi/$SITE/g" -e "s/esp32_001/$DEVICE/g" \
    "$APP/infrastructure/mqtt/aclfile" | write_file /etc/mosquitto/aclfile
run chown mosquitto:mosquitto /etc/mosquitto/aclfile
run chmod 640 /etc/mosquitto/aclfile

say "6/9 broker listener and firewall"

run install -m 644 "$APP/infrastructure/mqtt/furnace.conf" \
    /etc/mosquitto/conf.d/furnace.conf
run systemctl enable mosquitto
run systemctl restart mosquitto

if [ "$DRY_RUN" != 1 ]; then
    sleep 1
    ss -ltn 2>/dev/null | grep -q ':1883 ' ||
        die "mosquitto is not listening on 1883: journalctl -u mosquitto -n 30"
    printf '   listening on 1883\n'
fi

if [ "$DRY_RUN" = 1 ]; then
    run ufw allow from "$LAN_CIDR" to any port 1883 proto tcp
elif command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q 'Status: active'; then
    run ufw allow from "$LAN_CIDR" to any port 1883 proto tcp
elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    run firewall-cmd --permanent --add-rich-rule=\
"rule family=ipv4 source address=$LAN_CIDR port port=1883 protocol=tcp accept"
    run firewall-cmd --reload
else
    printf '   no active ufw/firewalld: make sure %s can reach port 1883\n' "$LAN_CIDR"
fi

say "7/9 bridge environment"

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    run useradd -r -s /usr/sbin/nologin "$SERVICE_USER"
fi

run install -d -m 750 /etc/furnace

{
    printf '# Written by infrastructure/mqtt/setup-server.sh — edit and re-run it.\n'
    printf 'MQTT_HOST=127.0.0.1\n'
    printf 'MQTT_PORT=1883\n'
    printf 'MQTT_USERNAME=furnace-bridge\n'
    printf 'MQTT_PASSWORD=%s\n' "$BRIDGE_PASS"
    printf 'MQTT_TLS=0\n'
    printf 'MQTT_SITE=%s\n' "$SITE"
    printf 'API_URL=%s\n' "$API_URL"
    printf 'API_KEY=%s\n' "$API_KEY"
    printf 'API_TIMEOUT_S=10\n'
    printf 'API_RETRIES=3\n'
    printf 'MAX_FRAMES=600\n'
} | write_file /etc/furnace/mqtt-bridge.env

run chown "$SERVICE_USER:$SERVICE_USER" /etc/furnace/mqtt-bridge.env
run chmod 600 /etc/furnace/mqtt-bridge.env

say "8/9 bridge dependencies and service"

if [ ! -x "$APP/.venv/bin/python" ]; then
    run python3 -m venv "$APP/.venv"
fi

run "$APP/.venv/bin/pip" install --quiet paho-mqtt

if [ "$DRY_RUN" != 1 ]; then
    su -s /bin/sh -c "test -r '$APP/backend/mqtt/bridge.py'" "$SERVICE_USER" 2>/dev/null ||
        printf '   warning: %s cannot read %s — fix the permissions\n' \
            "$SERVICE_USER" "$APP/backend/mqtt/bridge.py"
fi

sed -e "s#/opt/furnace#$APP#g" \
    -e "s/^User=.*/User=$SERVICE_USER/" \
    -e "s/^Group=.*/Group=$SERVICE_USER/" \
    "$APP/infrastructure/mqtt/furnace-mqtt-bridge.service" |
    write_file /etc/systemd/system/furnace-mqtt-bridge.service

run systemctl daemon-reload
run systemctl enable furnace-mqtt-bridge
run systemctl restart furnace-mqtt-bridge

say "9/9 verification"

if [ "$DRY_RUN" = 1 ]; then
    printf '   (dry run: nothing was checked)\n'
else
    sleep 2

    systemctl is-active --quiet furnace-mqtt-bridge ||
        die "the bridge is not running: journalctl -u furnace-mqtt-bridge -n 30"

    journalctl -u furnace-mqtt-bridge -n 6 --no-pager || true

    printf '\n   API health: '
    curl -sS -m 10 -o /dev/null -w '%{http_code}\n' "$API_BASE/api/health" ||
        printf 'unreachable\n'

    systemctl is-active --quiet mosquitto || die "mosquitto stopped"
    printf '   broker: active on 1883\n'
fi

# The board reaches this host on the LAN, so print the address it will use.
LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')"
[ -n "${LAN_IP:-}" ] || LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

cat <<SUMMARY

Done. Paste this into config.py on the board:

    MQTT_SITE = "$SITE"
    MQTT_DEVICE_ID = "$DEVICE"
    MQTT_HOST = "$LAN_IP"   # this server on the LAN
    MQTT_PORT = 1883
    MQTT_USER = MQTT_DEVICE_ID
    MQTT_PASSWORD = "$DEVICE_PASS"

Then, to prove the path end to end with no hardware:

    mosquitto_pub -h 127.0.0.1 -u '$DEVICE' -P '$DEVICE_PASS' \\
      -t factory/$SITE/$DEVICE/telemetry -q 1 -f /tmp/telemetry.json
    curl -s '$API_BASE/api/live?since=0' | head -c 400

Watch it:

    journalctl -u furnace-mqtt-bridge -f
    mosquitto_sub -h 127.0.0.1 -u furnace-bridge -P '<bridge password>' \\
      -t 'factory/$SITE/$DEVICE/telemetry' -v

Undo everything:

    systemctl disable --now furnace-mqtt-bridge
    rm /etc/systemd/system/furnace-mqtt-bridge.service /etc/furnace/mqtt-bridge.env
    rm /etc/mosquitto/conf.d/furnace.conf
    systemctl restart mosquitto

The manual walkthrough, TLS on 8883 and the troubleshooting table are in
docs/mqtt-setup.md.
SUMMARY
