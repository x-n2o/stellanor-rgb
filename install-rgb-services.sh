#!/usr/bin/env bash
# Install the persistent localhost SDK and stats service; enable stats at login.
set -euo pipefail
repo=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
base=${LIGHTS_OPENRGB_DIR:-"$HOME/.local/opt/openrgb"}
python=${LIGHTS_STATS_PYTHON:-"$HOME/.local/opt/stellanor-rgb-venv/bin/python"}
pearl_repo=${LIGHTS_PEARL_REPO:-"$HOME/src/stellanor-mining"}
[[ -f $pearl_repo/pearl_theme.py ]]
state=${XDG_STATE_HOME:-"$HOME/.local/state"}/chassis-lights
[[ -x $python && -x $base/squashfs-root/AppRun ]]
mkdir -p "$HOME/.config/systemd/user" "$state"
temp=$(mktemp -d)
trap 'rm -rf "$temp"' EXIT
cat > "$temp/stellanor-openrgb.service" <<EOF
[Unit]
Description=Stellanor localhost OpenRGB SDK
After=multi-user.target

[Service]
Type=simple
ExecStart="$base/squashfs-root/AppRun" --config "$base/config" --noautoconnect --server --server-host 127.0.0.1 --server-port 6742
Restart=on-failure
RestartSec=3
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
EOF
cat > "$temp/stellanor-rgb-stats.service" <<EOF
[Unit]
Description=Stellanor synchronized Pearl payout RGB
Requires=stellanor-openrgb.service
After=stellanor-openrgb.service

[Service]
Type=notify
NotifyAccess=main
TimeoutStartSec=45
ExecStart="$python" "$repo/rgb-stats.py" --status-file "$state/stats.json" --pearl-status "${XDG_STATE_HOME:-$HOME/.local/state}/pearl-watch-trcc/status.json" --pearl-theme "$pearl_repo/pearl_theme.py"
Restart=on-failure
RestartSec=5
TimeoutStopSec=10
EOF
cat > "$HOME/.config/systemd/user/stellanor-rgb-stats.service" <<'EOF'
[Unit]
Description=Start Stellanor RGB stats at login

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/sudo -n /usr/bin/systemctl start stellanor-rgb-stats.service
ExecStop=/usr/bin/sudo -n /usr/bin/systemctl stop stellanor-rgb-stats.service
TimeoutStartSec=60
TimeoutStopSec=15

[Install]
WantedBy=default.target
EOF
sudo -n install -m 644 "$temp/stellanor-openrgb.service" "$temp/stellanor-rgb-stats.service" /etc/systemd/system/
sudo -n systemctl daemon-reload
systemctl --user daemon-reload
systemctl --user enable --now stellanor-rgb-stats.service
printf 'stats\n' > "$state/state"
echo 'RGB stats enabled at login.'
