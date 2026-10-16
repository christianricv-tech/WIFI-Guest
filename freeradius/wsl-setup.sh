#!/usr/bin/env bash
# ============================================================
# freeradius/wsl-setup.sh — install & configure FreeRADIUS inside WSL (Ubuntu)
# for the Aruba Voucher Backend.
#
# Run this INSIDE the Ubuntu WSL distro, from anywhere:
#
#   sudo bash /mnt/c/aruba-voucher-backend/freeradius/wsl-setup.sh \
#        <AP_IP_ADDRESS> <RADIUS_SHARED_SECRET>
#
# Example:
#   sudo bash /mnt/c/aruba-voucher-backend/freeradius/wsl-setup.sh 192.168.1.50 s3cret-radius
#
# It installs FreeRADIUS, deploys the repo's config templates, enables the
# rlm_rest virtual server, points the REST module at the FastAPI backend on the
# Windows host, and validates the configuration before (re)starting the service.
#
# Networking assumption: WSL is running in MIRRORED mode so that 127.0.0.1 in
# WSL reaches the Windows host (see C:\Users\<you>\.wslconfig -> networkingMode
# = mirrored). If you use the default NAT mode, change CONNECT_URI below to the
# Windows host IP.
# ============================================================

set -euo pipefail

AP_IP="${1:-}"
SHARED_SECRET="${2:-}"

if [[ -z "$AP_IP" || -z "$SHARED_SECRET" ]]; then
    echo "Usage: sudo bash wsl-setup.sh <AP_IP_ADDRESS> <RADIUS_SHARED_SECRET>" >&2
    exit 1
fi

REPO_DIR="/mnt/c/aruba-voucher-backend"
FR_DIR="/etc/freeradius/3.0"
CONNECT_URI="http://127.0.0.1:8000"   # mirrored networking: host-localhost

echo "==> Installing FreeRADIUS packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y freeradius freeradius-rest freeradius-postgresql

echo "==> Deploying rlm_rest module"
install -m 644 "$REPO_DIR/freeradius/mods-available/rest" "$FR_DIR/mods-available/rest"
sed -i "s|^\(\s*connect_uri\s*=\s*\).*|\1\"$CONNECT_URI\"|" "$FR_DIR/mods-available/rest"

echo "==> Deploying voucher virtual server"
install -m 644 "$REPO_DIR/freeradius/sites-available/voucher" "$FR_DIR/sites-available/voucher"

echo "==> Enabling modules and site (symlinks)"
ln -sf "$FR_DIR/mods-available/rest"    "$FR_DIR/mods-enabled/rest"
ln -sf "$FR_DIR/sites-available/voucher" "$FR_DIR/sites-enabled/voucher"

echo "==> Configuring RADIUS client (Aruba AP $AP_IP)"
# Remove any previous block we added, then append the real one.
sed -i '/^client aruba_ap {/,/^}/d' "$FR_DIR/clients.conf" 2>/dev/null || true
cat >> "$FR_DIR/clients.conf" <<EOF

client aruba_ap {
    ipaddr    = $AP_IP
    secret    = $SHARED_SECRET
    shortname = aruba-ap-505
}
EOF

echo "==> Validating FreeRADIUS configuration"
if ! freeradius -XC; then
    echo "!! Configuration check failed. Fix the errors above before starting." >&2
    exit 1
fi

echo "==> Restarting FreeRADIUS"
systemctl enable freeradius
systemctl restart freeradius
systemctl --no-pager --full status freeradius | head -n 15

echo
echo "FreeRADIUS is up. Test locally with:"
echo "  echo 'User-Name=CODE,User-Password=CODE' | radtest CODE CODE 127.0.0.1 0 '$SHARED_SECRET'"
echo
echo "Remember to allow inbound UDP 1812 through the Windows firewall (see README)."
