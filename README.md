# stellanor-rgb

Synchronized Pearl payout lighting for Kingston Fury DDR5, ASUS ROG Astral
RTX 5090, ASUS Aura chassis/fan headers, Razer Basilisk V3 Pro 35K,
and BlackWidow V4 Pro, with Thermalright LCD control. OpenRGB identifies the
connected 00d7 mouse through its Phantom Green wireless detector.

```sh
lights.sh on     # Payout progress RGB and Pearl LCD
lights.sh stats  # Payout progress RGB only (LCD unchanged)
lights.sh off    # Stop animation, turn all detected RGB off, sleep the LCD
lights.sh        # Toggle, accounting for services started at login
```

Each LED zone fills with the same Pearl payout progress. Cyan marks progress,
green marks the payout threshold reached, and amber marks stale pool data.
A shared twelve-second colour sweep and four-second pulse animate the filled
portion and partial leading LED. The sweep continues above the payout threshold
while rewards mature, so completion never leaves the lights at one fixed colour.
Unfilled LEDs remain dim. Rewards still maturing count towards progress, just
as in `pearl watch`; a full bar can remain full while awaiting confirmation.

The controller reads `pearl-watch-trcc/status.json`, emitted by Pearl's shared
monitor. It makes no pool requests or utilization queries. The same
`payout_progress` calculation drives the terminal, LCD and RGB. The controller
loads `pearl_theme.py` directly from that checkout for the palette and pulse
shared with the LCD. Override its location with `LIGHTS_PEARL_REPO` at install. Install and
run `pearl watch --install-service` in the stellanor-mining project first.

All detected Direct-mode accessories use one SDK connection and animation clock.
Each ASUS ARGB header is configured to its supported maximum of 120 slots at
runtime; this is not a measured physical chain length. USB/SMBus writes are
sequential, so synchronization is approximate at the transport level.

`off` stops Pearl before sleeping the panel. `on` starts Pearl and synchronized
RGB again. Legacy blank-display and TRCC TPS writers are stopped before TRCC
operations. The legacy display writer is used only when TRCC is absent.

## Setup

Requires systemd, Bash, `flock`, noninteractive `sudo`, Python 3, liquidctl,
and OpenRGB 1.0. The legacy display path also needs PyUSB and Pillow.

```sh
sudo apt-get install liquidctl python3-usb python3-pil
uv venv --system-site-packages ~/.local/opt/stellanor-rgb-venv
uv pip install --python ~/.local/opt/stellanor-rgb-venv/bin/python openrgb-python==0.3.8
```

Download the Linux AppImage from [OpenRGB's releases](https://openrgb.org/releases.html),
place it in `~/.local/opt/openrgb`, and extract it there:

```sh
cd ~/.local/opt/openrgb
chmod +x OpenRGB.AppImage
./OpenRGB.AppImage --appimage-extract
```

From this repository:

```sh
mkdir -p ~/.local/opt/openrgb/config ~/.local/bin
cp OpenRGB.json ~/.local/opt/openrgb/config/OpenRGB.json
ln -s "$(pwd)/lights.sh" ~/.local/bin/lights.sh
./install-rgb-services.sh
```

The installer starts stats immediately and enables its user service for
subsequent logins. The user service starts the root hardware service, which
starts an OpenRGB SDK bound exclusively to `127.0.0.1:6742`. Hardware detection
happens once per SDK process. Manual commands reuse that SDK when running.
OpenRGB owns all RGB writes during animation. Manual off commands stop the
animation first and include every detected USB accessory. The SDK binds only
to localhost. The supplied detector settings include the connected Razer models.

Run `lights.sh` as your normal user. Set `LIGHTS_OPENRGB_DIR` or
`LIGHTS_STATS_PYTHON` before installation to override the local tools.

State, logs, and live RGB telemetry live in `$XDG_STATE_HOME/chassis-lights`
(default `~/.local/state/chassis-lights`). Toggle tracks the last successful
command, including stats as an on state. Check background services with:

```sh
systemctl --user status stellanor-rgb-stats.service
sudo systemctl status stellanor-rgb-stats.service stellanor-openrgb.service
cat ~/.local/state/chassis-lights/stats.json
```

## Validation

```sh
python3 -m unittest discover -s tests -v
bash -n lights.sh install-rgb-services.sh

```

Tests cover payout fill and stale data, phase synchronization, controller writes,
service ownership and TRCC coordination with fake hardware commands. Successful
controller writes verify the
software path; visible colors and panel output require direct observation.

Protocol references:
[OpenRGB SDK](https://github.com/CalcProgrammer1/OpenRGB/blob/master/Documentation/OpenRGBSDK.md),
[liquidctl ASUS Aura guide](https://github.com/liquidctl/liquidctl/blob/main/docs/asus-aura-led-guide.md),
[TRCC Linux](https://github.com/Lexonight1/thermalright-trcc-linux).

Only source, detector configuration, documentation, and tests belong in
this repository. Runtime logs, USB responses, serial numbers, account
configuration, and downloaded binaries are excluded.
