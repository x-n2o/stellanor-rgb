#!/usr/bin/env bash
# Control RAM, GPU, and motherboard-connected chassis/cooler RGB.
# No argument toggles the last successfully applied state. On uses rainbow.
set -euo pipefail

if (( $# > 1 )) || [[ ${1:-toggle} != @(on|off|toggle) ]]; then
    echo "Usage: lights.sh [on|off] (no argument toggles)" >&2
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
    if [[ $previous == on ]]; then action=off; else action=on; fi
fi

runner=()
(( EUID == 0 )) || runner=(sudo)
common=(--config "$base/config" --noautoconnect)
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
if ! "${runner[@]}" "$base/squashfs-root/AppRun" "${args[@]}" >"$state_dir/last-run.log" 2>&1; then
    cat "$state_dir/last-run.log" >&2
    exit 1
fi
if [[ $action == on ]]; then aura_mode=rainbow; else aura_mode=off; fi
"${runner[@]}" liquidctl --match 'ASUS Aura' --pick 0 set sync color "$aura_mode"
"${runner[@]}" /usr/bin/python3 "$script_dir/thermalright-display.py" "$action"
printf '%s\n' "$action" > "$state_dir/state"
echo "Lights $action"
