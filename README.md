# stellanor-rgb

A small Linux command for switching chassis RGB, Kingston Fury DDR5 RGB,
ASUS ROG Astral RTX 5090 RGB, and a Thermalright USB display on and off.

```sh
lights.sh on     # Rainbow RGB; restore the display's built-in logo
lights.sh off    # RGB off; display an all-black image
lights.sh        # Toggle the last successfully applied state
```

The LCD is blanked with a continuous stream of black JPEG frames; its backlight
is not powered down. `off` starts a transient systemd service named
`stellanor-display-blank.service`; `on` stops that stream and resets the display.
The JPEG is encoded once and reused, without GPU rendering.
The supported display is the ChiZhu `87ad:70db` SSCRM panel reporting model
7 at response byte 32, using 640×480 JPEG frames. Other panels are rejected.
It does not change fan speeds, GPU workloads, or cooling settings.

## Setup

Requires systemd, Bash, `flock`, `sudo`, Python 3, PyUSB, Pillow, liquidctl, and OpenRGB 1.0.
On Ubuntu, install the Python dependencies with:

```sh
sudo apt-get install python3-usb python3-pil liquidctl
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
chmod +x lights.sh
ln -s "$(pwd)/lights.sh" ~/.local/bin/lights.sh
lights.sh off
```

Run as your normal user; the script invokes `sudo` for hardware access.
Set `LIGHTS_OPENRGB_DIR` if OpenRGB lives elsewhere. Ensure `~/.local/bin`
is on your PATH. The bundled detector configuration enables only the three
supported RGB controller families; adapt it and the device names in
`lights.sh` for different hardware.

Toggle state and diagnostic logs live in `$XDG_STATE_HOME/chassis-lights`
(default `~/.local/state/chassis-lights`). Toggle tracks this script's last
successful command, not changes made by other RGB software. The script
does not automatically run at boot. Its transient display service runs only
while off is selected and is removed when stopped. Check it with
`systemctl status stellanor-display-blank.service`.

## Validation

```sh
python3 -m unittest discover -s tests -v
bash -n lights.sh
```

The hardware workflow was checked on the supported RGB controllers and
the screen's black output was visually confirmed. Automated tests use fake
hardware commands and do not access USB or SMBus.

Protocol references:
[TRCC USBLCDNEW documentation](https://github.com/Lexonight1/thermalright-trcc-linux/blob/main/doc/PROTOCOL_USBLCDNEW.md),
[Thermalright LCD Control](https://github.com/rejeb/thermalright-lcd-control).

Only source, detector configuration, documentation, and tests belong in
this repository. Runtime logs, USB responses, serial numbers, account
configuration, and downloaded binaries are excluded.
