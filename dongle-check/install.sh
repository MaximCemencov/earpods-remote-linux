#!/bin/bash
# Install the Apple adapter check (needs sudo). Idempotent.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
sudo install -m 755 "$here/apple-dongle-check.sh" /usr/local/libexec/apple-dongle-check.sh
sudo install -m 644 "$here/apple-dongle-check.service" /etc/systemd/system/apple-dongle-check.service
sudo install -m 644 "$here/73-apple-dongle.rules" /etc/udev/rules.d/73-apple-dongle.rules
command -v restorecon >/dev/null && sudo restorecon /usr/local/libexec/apple-dongle-check.sh || true
sudo udevadm control --reload
sudo systemctl daemon-reload
