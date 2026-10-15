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

All RGB devices use the shared configurable colour during progress and after
the payout threshold is reached. Stale pool data uses amber, or the configured
colour when monochrome mode is enabled. Brightness
tracks progress towards the next whole PRL: a balance of 2.25 PRL gives 25%
brightness, 2.99 gives 99%, and 3.00 resets to 0%, with no minimum. The controller
uses TRCC's `next_prl_progress`, falling back to fractional total pool balance
for older snapshots. Rewards still maturing count towards progress.

The GPU, BlackWidow V4 Pro and Basilisk V3 Pro 35K fill left to right to that
same percentage. Every sweep starts with the entire device black. A 100%-bright
wave reveals the filled section, leaving passed LEDs at the progress-proportional
brightness; LEDs ahead of the wave and beyond the endpoint stay off. For example,
at 50% the wave reveals the left half, which settles to 50% brightness while the
right half remains off. A partially filled boundary LED settles proportionally
dimmer. The completed bar holds during the pause, then clears for the next sweep.

Each sweep takes one second. The following pause decreases linearly from two
seconds at 0% progress to 0.3 seconds at 100%. Sweep timing and length are latched
at cycle boundaries, so live progress updates cannot jump the wave backwards or
pre-illuminate newly added positions before the next sweep. Reveal timing starts
from the emitted black frame; delayed updates extend the cycle so a visible wave
appears before another reset. RAM and chassis retain their existing clock.
Keyboard columns,
side lighting and the U-shaped wrist rest share the horizontal axis; the mouse
uses horizontal coordinates across both sides of its perimeter and its wheel.
Keyboard matrix holes and Void slots remain black. Each accessory receives one
complete frame so all of its zones share a single wave position.

The two Kingston Fury sticks, onboard motherboard LEDs and addressable fan/header
chains use the same ripple in their reported LED order, independently for each
zone. Every LED retains the next-PRL background brightness without a minimum,
while the full-brightness ripple stops at the
current percentage. RAM and motherboard updates send one complete frame per
controller to avoid redundant queued writes. ARGB headers still use their
driver-reported 120 slots; physical chain lengths and direction require visual
confirmation, so some of a sweep may traverse unused slots.

The controller reads `pearl-watch-trcc/status.json`, emitted by Pearl's shared
monitor. It makes no pool requests or utilization queries. The same
`payout_progress` calculation drives the terminal, LCD and RGB. The controller
loads `pearl_theme.py` directly from that checkout for the palette and brightness
scaling; update that module alongside this controller. Override its location with
`LIGHTS_PEARL_REPO` at install. Install and
run `pearl watch --install-service` in the stellanor-mining project first.

Configure both RGB and the complete TRCC dashboard from the mining checkout:

```sh
uv run python -m pearl_theme --color red --monochrome
uv run python -m pearl_theme --color '#ff6600' --no-monochrome
uv run python -m pearl_theme --reset
```

Settings persist in `theme.json` beside `--pearl-status` and reload automatically.
Use `--theme-config PATH` for a different shared settings file. Monochrome mode
tints all LCD elements and RGB warnings with the selected colour; disabling it
retains the chosen accent while restoring the other LCD colours and amber warnings.

All detected Direct-mode accessories use one SDK connection and base brightness
value; all supported devices overlay full-brightness ripple highlights.
Only changed frames are sent. Each device update is read back before sending
more, so slower RAM and keyboard controllers cannot accumulate old animation
frames while faster case lighting moves ahead. This confirms the SDK state;
physical colour matching still depends on each device's LEDs.
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

When OpenRazer/Polychromatic is installed alongside this controller, set these
options in `~/.config/openrazer/razer.conf`, then restart the user daemon with
`systemctl --user restart openrazer-daemon.service` before starting RGB sync:

```ini
[Startup]
devices_off_on_screensaver = False
battery_notifier = False
restore_persistence = False
```

OpenRazer's lock-screen blackout otherwise sets Razer brightness to zero while
OpenRGB continues sending animation frames. Background battery polling can also
collide with OpenRGB's direct USB traffic. Before using Polychromatic, stop both
`stellanor-rgb-stats.service` and `stellanor-openrgb.service`. If a keyboard is
already dark, restore brightness in Polychromatic while those services are
stopped. Start `stellanor-rgb-stats.service` again to resume synchronized lighting.
These are the system services controlled with `sudo systemctl`.

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
