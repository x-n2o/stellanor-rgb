#!/usr/bin/env bash
# Control RAM, GPU, and motherboard-connected chassis/cooler RGB.
# No argument toggles the last successfully applied state. On uses synchronized Pearl payout progress when installed; stats starts RGB alone.
set -euo pipefail

if (( $# > 1 )) || [[ ${1:-toggle} != @(on|off|toggle|stats) ]]; then
    echo "Usage: lights.sh [on|off|stats] (no argument toggles)" >&2
    exit 2
fi

base=${LIGHTS_OPENRGB_DIR:-"$HOME/.local/opt/openrgb"}
script_dir=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
state_dir=${XDG_STATE_HOME:-"$HOME/.local/state"}/chassis-lights
mkdir -p "$state_dir"
exec 9>"$state_dir/lock"
flock 9
action=${1:-toggle}
if [[ $action == toggle ]]; then
    previous=off
    [[ ! -f $state_dir/state ]] || read -r previous < "$state_dir/state"
    # Login may have restarted RGB stats or the Pearl LCD since the last off.
    if systemctl is-active --quiet stellanor-rgb-stats.service ||
       systemctl --user is-active --quiet pearl-watch-trcc.service; then
        previous=on
    fi
    if [[ $previous == on || $previous == stats ]]; then action=off; else action=on; fi
fi

runner=()
(( EUID == 0 )) || runner=(sudo)
if [[ $action == stats ]]; then
    if [[ $(systemctl --user show stellanor-rgb-stats.service -p LoadState --value) != loaded ]]; then
        echo "Run $script_dir/install-rgb-services.sh before selecting stats." >&2
        exit 1
    fi
    systemctl --user start stellanor-rgb-stats.service
    # Restart the root service if it was stopped outside the user wrapper.
    "${runner[@]}" systemctl start stellanor-rgb-stats.service
    printf 'stats\n' > "$state_dir/state"
    echo 'Lights stats'
    exit 0
fi
# Release the persistent Aura handle before any manual lighting writes.
if [[ $(systemctl --user show stellanor-rgb-stats.service -p LoadState --value 2>/dev/null) == loaded ]]; then
    systemctl --user stop stellanor-rgb-stats.service
    "${runner[@]}" systemctl stop stellanor-rgb-stats.service
fi
common=(--config "$base/config" --noautoconnect)
# Reuse the SDK's detected devices when it is running: no second hardware scan.
if "${runner[@]}" systemctl is-active --quiet stellanor-openrgb.service; then
    common=(--client 127.0.0.1:6742 --nodetect)
fi
"${runner[@]}" "$base/squashfs-root/AppRun" "${common[@]}" --list-devices >"$state_dir/last-detection.log" 2>&1
for device in 'Kingston Fury DDR5 DRAM' 'ASUS ROG ASTRAL GeForce RTX 5090 OC' 'ASUS ROG STRIX X870-F GAMING WIFI'; do
    if ! grep -Fq ": $device" "$state_dir/last-detection.log"; then
        echo "Lighting controller missing: $device" >&2
        cat "$state_dir/last-detection.log" >&2
        exit 1
    fi
done
args=("${common[@]}")
for device in 'Kingston Fury DDR5 DRAM' 'ASUS ROG ASTRAL GeForce RTX 5090 OC' 'ASUS ROG STRIX X870-F GAMING WIFI'; do
    args+=(--device "$device")
    if [[ $action == on ]]; then
        args+=(--mode Rainbow)
    elif [[ $device == 'Kingston Fury DDR5 DRAM' ]]; then
        args+=(--mode Static --color 000000)
    else
        args+=(--mode Off)
    fi
done
# Include directly detected USB accessories in manual on/off commands.
while IFS= read -r device; do
    case "$device" in
        'Kingston Fury DDR5 DRAM'|'ASUS ROG ASTRAL GeForce RTX 5090 OC'|'ASUS ROG STRIX X870-F GAMING WIFI') continue ;;
    esac
    args+=(--device "$device" --mode Direct --color 000000)
done < <(sed -n 's/^[0-9][0-9]*: //p' "$state_dir/last-detection.log" | sort -u)
if ! "${runner[@]}" "$base/squashfs-root/AppRun" "${args[@]}" >"$state_dir/last-run.log" 2>&1; then
    cat "$state_dir/last-run.log" >&2
    exit 1
fi
if [[ $action == on ]]; then aura_mode=rainbow; else aura_mode=off; fi
"${runner[@]}" liquidctl --match 'ASUS Aura' --pick 0 set sync color "$aura_mode"
# Use the shared TRCC daemon on native panels; never start the legacy writer
# while TRCC owns the display. Pearl's service takes care of waking the panel.
trcc_bin=${LIGHTS_TRCC_BIN:-$(command -v trcc || true)}
if [[ -n $trcc_bin ]]; then
    # Old writers can restart independently and contend with TRCC for USB.
    if [[ $(systemctl show stellanor-display-blank.service -p LoadState --value 2>/dev/null) == loaded ]]; then
        "${runner[@]}" systemctl stop stellanor-display-blank.service
    fi
    if [[ $(systemctl --user show trcc-tps.service -p LoadState --value 2>/dev/null) == loaded ]]; then
        systemctl --user stop trcc-tps.service
    fi
    if [[ $(systemctl --user show pearl-watch-trcc.service -p LoadState --value 2>/dev/null) == loaded ]]; then
        if [[ $action == on ]]; then
            systemctl --user start pearl-watch-trcc.service
        else
            systemctl --user stop pearl-watch-trcc.service
            TRCC_DAEMON=1 "$trcc_bin" display sleep 87ad:70db
        fi
    elif [[ $action == off ]]; then
        TRCC_DAEMON=1 "$trcc_bin" display sleep 87ad:70db
    else
        TRCC_DAEMON=1 "$trcc_bin" display restore-theme 87ad:70db
    fi
else
    "${runner[@]}" /usr/bin/python3 "$script_dir/thermalright-display.py" "$action"
fi
if [[ $action == on && $(systemctl --user show stellanor-rgb-stats.service -p LoadState --value 2>/dev/null) == loaded ]]; then
    systemctl --user start stellanor-rgb-stats.service
    "${runner[@]}" systemctl start stellanor-rgb-stats.service
fi
printf '%s\n' "$action" > "$state_dir/state"
echo "Lights $action"
