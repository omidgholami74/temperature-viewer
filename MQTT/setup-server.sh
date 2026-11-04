#!/usr/bin/env bash
#
# setup-server.sh — put the furnace MQTT stack (broker + bridge) on the
# internal Linux server that already runs the PHP API.
#
#   sudo bash MQTT/setup-server.sh
#
# What it does, in order:
#   1. installs mosquitto, mosquitto-clients and python3-venv
#   2. creates the broker accounts: `bridge` (consumer) and one per board
#   3. installs the ACL from broker/acl.example, rewritten for this site/device
#   4. enables the production listener (broker/mosquitto.production.conf)
#   5. opens 1883 only to the board's subnet (when ufw or firewalld is active)
#   6. creates MQTT/bridge/.venv and installs paho-mqtt
#   7. writes /etc/furnace/mqtt-bridge.env with the passwords and API key
#   8. installs deploy/furnace-mqtt-bridge.service and starts it
#   9. checks the broker, the service, the bridge status topic and /api/health
#
# It is idempotent: a second run rewrites the files and rotates the passwords.
# The API, its SQLite file and the dashboards are not modified — every reading
# still goes through the same POST /api/ingest.
#
# Optional overrides (default: ask, or a sensible default):
#   sudo SITE=razi DEVICE=kiln-1 API_URL=https://apitemp.example.com/api/ingest \
#        LAN_CIDR=10.20.0.0/16 SERVICE_USER=furnace APP=/srv/furnace \
#        bash MQTT/setup-server.sh
#
# Passwords are read with `read -rsp`, so they never reach the shell history.
# Pass BRIDGE_PASS / DEVICE_PASS / API_KEY in the environment to skip a prompt.
#
#   DRY_RUN=1 bash MQTT/setup-server.sh        # print the plan, change nothing
#
set -euo pipefail

APP="${APP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MQTT_DIR="$APP/MQTT"
SERVICE_USER="${SERVICE_USER:-furnace}"
LAN_CIDR="${LAN_CIDR:-192.168.1.0/24}"
DRY_RUN="${DRY_RUN:-0}"

# The protocol allows lowercase ids only: they are topic levels and usernames.
IDENT='^[a-z0-9][a-z0-9_-]{1,31}$'

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

say() {
    printf '\n== %s\n' "$*"
}

run() {
    if [ "$DRY_RUN" = 1 ]; then
        printf '   + %s\n' "$*"
    else
        "$@"
    fi
}

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

    [ -n "$value" ] || value="$fallback"
    eval "$name=\"\$value\""
}

say "1/9 preflight"

for required in \
    "$MQTT_DIR/bridge/furnace_bridge.py" \
    "$MQTT_DIR/bridge/requirements.txt" \
    "$MQTT_DIR/broker/acl.example" \
    "$MQTT_DIR/broker/mosquitto.production.conf" \
    "$MQTT_DIR/deploy/furnace-mqtt-bridge.service"; do
    [ -f "$required" ] || die "missing $required — pass APP=/path/to/repo"
done

if [ "$DRY_RUN" != 1 ]; then
    [ "$(id -u)" = 0 ] || die "run it with sudo: it writes /etc/mosquitto and /etc/furnace"
    command -v apt-get >/dev/null 2>&1 ||
        die "this script targets Debian/Ubuntu; see MQTT/docs/operations.md for other distros"
fi

say "2/9 settings"

ask SITE "site name (topic level and ACL scope) [lab]: " "lab"
ask DEVICE "board id [kiln-1]: " "kiln-1"
ask API_URL "ingest URL [http://127.0.0.1/api/ingest]: " "http://127.0.0.1/api/ingest"
ask API_KEY "FURNACE_INGEST_KEY from the API config (empty when the API is open): " "" 1

[[ "$SITE" =~ $IDENT ]] || die "SITE must match $IDENT (got '$SITE')"
[[ "$DEVICE" =~ $IDENT ]] || die "DEVICE must match $IDENT (got '$DEVICE')"
[[ "$API_URL" == http*://* ]] || die "API_URL must be an http(s) URL (got '$API_URL')"

ask BRIDGE_PASS "new password for the broker user 'bridge': " "" 1
ask DEVICE_PASS "new password for the broker user '$DEVICE': " "" 1

if [ "$DRY_RUN" != 1 ]; then
    [ -n "$BRIDGE_PASS" ] || die "the bridge password cannot be empty"
    [ -n "$DEVICE_PASS" ] || die "the board password cannot be empty"
    [ "$BRIDGE_PASS" != "$DEVICE_PASS" ] || die "use two different passwords"
fi

printf '   site=%s device=%s api=%s\n' "$SITE" "$DEVICE" "$API_URL"
printf '   app=%s user=%s lan=%s\n' "$APP" "$SERVICE_USER" "$LAN_CIDR"

say "3/9 packages"

if [ "$DRY_RUN" = 1 ] || ! command -v mosquitto >/dev/null 2>&1; then
    run apt-get update -qq
    run apt-get install -y mosquitto mosquitto-clients python3-venv
elif ! python3 -c 'import venv' 2>/dev/null; then
    run apt-get install -y python3-venv
fi

say "4/9 broker accounts"

# -b alone fails on a missing file, so the first account creates it with -c.
if [ -s /etc/mosquitto/passwd ]; then
    run mosquitto_passwd -b /etc/mosquitto/passwd bridge "$BRIDGE_PASS"
else
    run mosquitto_passwd -c -b /etc/mosquitto/passwd bridge "$BRIDGE_PASS"
fi
run mosquitto_passwd -b /etc/mosquitto/passwd "$DEVICE" "$DEVICE_PASS"
run chown mosquitto:mosquitto /etc/mosquitto/passwd
run chmod 600 /etc/mosquitto/passwd

printf '   the dashboard account is separate: see MQTT/docs/operations.md\n'

say "5/9 ACL (a board may only write its own topics)"

sed -e "s/kiln-1/$DEVICE/g" -e "s#furnace/lab/#furnace/$SITE/#g" \
    "$MQTT_DIR/broker/acl.example" | write_file /etc/mosquitto/acl
run chown mosquitto:mosquitto /etc/mosquitto/acl
run chmod 640 /etc/mosquitto/acl

say "6/9 broker listener and firewall"

run install -m 644 "$MQTT_DIR/broker/mosquitto.production.conf" \
    /etc/mosquitto/conf.d/furnace-mqtt.conf
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
    printf '# Written by MQTT/setup-server.sh — edit and re-run it.\n'
    printf 'FURNACE_MQTT_HOST=127.0.0.1\n'
    printf 'FURNACE_MQTT_PORT=1883\n'
    printf 'FURNACE_MQTT_SITE=%s\n' "$SITE"
    printf 'FURNACE_MQTT_USERNAME=bridge\n'
    printf 'FURNACE_MQTT_PASSWORD=%s\n' "$BRIDGE_PASS"
    printf 'FURNACE_MQTT_CLIENT_ID=furnace-bridge-%s\n' "$SITE"
    printf 'FURNACE_MQTT_API_URL=%s\n' "$API_URL"
    printf 'FURNACE_MQTT_API_KEY=%s\n' "$API_KEY"
    printf 'FURNACE_MQTT_QUEUE_LIMIT=500\n'
    printf 'FURNACE_MQTT_REQUEST_TIMEOUT=10\n'
    printf 'FURNACE_MQTT_STATUS_INTERVAL=15\n'
    printf 'FURNACE_MQTT_LOG_LEVEL=INFO\n'
} | write_file /etc/furnace/mqtt-bridge.env

run chown "$SERVICE_USER:$SERVICE_USER" /etc/furnace/mqtt-bridge.env
run chmod 600 /etc/furnace/mqtt-bridge.env

say "8/9 bridge dependencies and service"

if [ ! -x "$MQTT_DIR/bridge/.venv/bin/python" ]; then
    run python3 -m venv "$MQTT_DIR/bridge/.venv"
fi

run "$MQTT_DIR/bridge/.venv/bin/pip" install --quiet -r "$MQTT_DIR/bridge/requirements.txt"

if [ "$DRY_RUN" != 1 ]; then
    su -s /bin/sh -c "test -r '$MQTT_DIR/bridge/furnace_bridge.py'" "$SERVICE_USER" 2>/dev/null ||
        printf '   warning: %s cannot read %s — fix the permissions\n' \
            "$SERVICE_USER" "$MQTT_DIR/bridge/furnace_bridge.py"
fi

sed "s#@APP@#$APP#g" "$MQTT_DIR/deploy/furnace-mqtt-bridge.service" |
    write_file /etc/systemd/system/furnace-mqtt-bridge.service

run systemctl daemon-reload
run systemctl enable furnace-mqtt-bridge
run systemctl restart furnace-mqtt-bridge

say "9/9 verification"

if [ "$DRY_RUN" = 1 ]; then
    printf '   (dry run: nothing was checked)\n'
else
    sleep 3

    systemctl is-active --quiet furnace-mqtt-bridge ||
        die "the bridge is not running: journalctl -u furnace-mqtt-bridge -n 30"

    journalctl -u furnace-mqtt-bridge -n 8 --no-pager || true

    printf '\n   bridge status topic: '
    timeout 5 mosquitto_sub -h 127.0.0.1 -u bridge -P "$BRIDGE_PASS" \
        -t "furnace/$SITE/_bridge/status" -C 1 2>/dev/null ||
        printf '(not published yet — it refreshes every 15 s)\n'

    printf '   API health: '
    curl -sS -m 10 -o /dev/null -w '%{http_code}\n' "${API_URL%/api/ingest}/api/health" ||
        printf 'unreachable\n'

    systemctl is-active --quiet mosquitto || die "mosquitto stopped"
    printf '   broker: active on 1883\n'
fi

# The board reaches this host on the LAN, so print the address it will use.
LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')"
[ -n "${LAN_IP:-}" ] || LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

cat <<SUMMARY

Done. Construct the transport like this on the board
(MQTT/firmware/README.md has the full loop):

    from furnace_mqtt import MqttTransport

    transport = MqttTransport(
        site="$SITE",
        device="$DEVICE",
        host="$LAN_IP",          # this server on the LAN
        port=1883,
        username="$DEVICE",
        password="$DEVICE_PASS",
        batch_size=10,            # one second at 10 Hz
        buffer_limit=3600,        # ten minutes while the broker is away
    )

Prove the path end to end with no hardware:

    mosquitto_pub -h 127.0.0.1 -u '$DEVICE' -P '$DEVICE_PASS' \\
      -t furnace/$SITE/$DEVICE/samples -q 1 \\
      -m '{"v":1,"seq":1,"frames":[[1200,[700,700,null,700],[0,0,0,0]]]}'

    curl -s '${API_URL%/api/ingest}/api/live?since=0' | head -c 400

Watch it:

    journalctl -u furnace-mqtt-bridge -f
    mosquitto_sub -h 127.0.0.1 -u bridge -P '<bridge password>' \\
      -t 'furnace/$SITE/#' -v

Undo everything:

    systemctl disable --now furnace-mqtt-bridge
    rm /etc/systemd/system/furnace-mqtt-bridge.service /etc/furnace/mqtt-bridge.env
    rm /etc/mosquitto/conf.d/furnace-mqtt.conf /etc/mosquitto/acl
    systemctl restart mosquitto

Hardening beyond this script — TLS on 8883, a read-only dashboard account,
certificates, monitoring and the runbooks — is in MQTT/docs/operations.md.
SUMMARY
