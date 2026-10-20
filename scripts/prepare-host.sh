#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Dependency bootstrap ONLY. Does not deploy nodes, obtain certificates or alter GCP.
set -eu
if [ "${1:-}" != "--confirm" ] || [ "$(id -u)" -ne 0 ]; then
  echo 'Run as root with --confirm only on a reviewed, dedicated Debian/Ubuntu host.' >&2
  exit 1
fi
if [ -e /etc/s-box/sb.json ]; then
  echo 'Existing proxy deployment: refusing automatic preparation.' >&2
  exit 1
fi
if command -v nginx >/dev/null 2>&1; then
  echo 'Nginx is already installed. Follow the manual dependency procedure; no existing site will be changed.' >&2
  exit 1
fi
if [ -e /opt/gcp-proxy-spark ]; then
  echo 'Existing Spark directory: inspect it and use the manual procedure.' >&2
  exit 1
fi
if ! command -v apt-get >/dev/null 2>&1 || [ ! -r /etc/os-release ]; then
  echo 'Debian/Ubuntu with apt required.' >&2
  exit 1
fi
. /etc/os-release
case "$ID" in debian|ubuntu) ;; *) echo 'Unsupported distribution.' >&2; exit 1;; esac
if command -v ss >/dev/null 2>&1 && ss -H -lnt | awk '{print $4}' | grep -Eq ':(80|443)$'; then
  echo 'Web port already occupied; refusing to install another web server.' >&2
  exit 1
fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip curl ca-certificates openssl nginx nftables
# Only this freshly installed stock Nginx is stopped. Preserve its stock symlink.
systemctl stop nginx
install -d -m 755 /opt/gcp-proxy-spark
if [ -L /etc/nginx/sites-enabled/default ]; then
  mv /etc/nginx/sites-enabled/default /opt/gcp-proxy-spark/stock-nginx-default
fi
python3 -m venv /opt/gcp-proxy-spark/venv
/opt/gcp-proxy-spark/venv/bin/pip install 'PyYAML==6.0.3'
python3 -m venv /opt/gcp-proxy-spark/certbot
/opt/gcp-proxy-spark/certbot/bin/pip install 'certbot==5.4.0'
echo 'Prerequisites prepared. No nodes deployed. Continue with init, plan and inspect.'
