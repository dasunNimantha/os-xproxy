#!/bin/sh

# os-xproxy installer for OPNsense
# Usage: fetch -o - https://raw.githubusercontent.com/dasunNimantha/os-xproxy/main/install.sh | sh

set -e

REPO="dasunNimantha/os-xproxy"
BRANCH="main"
BASE_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
PREFIX="/usr/local"

echo "==> Installing os-xproxy..."

cd /tmp
fetch -o os-xproxy.tar.gz "https://github.com/${REPO}/archive/refs/heads/${BRANCH}.tar.gz"
tar xzf os-xproxy.tar.gz
cd os-xproxy-${BRANCH}/src

find . -type f | while read FILE; do
    DIR=$(dirname "${PREFIX}/${FILE}")
    mkdir -p "${DIR}"
    cp "${FILE}" "${PREFIX}/${FILE}"
done

chmod +x "${PREFIX}/opnsense/scripts/xproxy/"*.py "${PREFIX}/opnsense/scripts/xproxy/"*.sh "${PREFIX}/opnsense/scripts/xproxy/"*.php 2>/dev/null || true

cd /tmp
rm -rf os-xproxy-${BRANCH} os-xproxy.tar.gz

echo "==> Restarting configd..."
service configd restart

echo "==> Done. Navigate to VPN > Xproxy in the OPNsense web UI."
