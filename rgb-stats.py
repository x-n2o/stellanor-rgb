#!/usr/bin/env python3
"""Synchronize connected RGB accessories with Pearl payout progress."""
import argparse
import importlib.util
import json
import logging
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

LOG = logging.getLogger("rgb-stats")
PERIOD = 4.0
FRAME_INTERVAL = 0.05
SAMPLE_INTERVAL = 1.0
CONFIRM_TIMEOUT = 10.0
GPU_WAVE_SWEEP = 3.0
GPU_WAVE_PERIOD = 4.0
GPU_WAVE_TAIL = 3


def color_for_load(percent):
    """Green at 0%, yellow at 50%, red at 100%."""
    value = max(0.0, min(100.0, percent)) / 100.0
    return (round(255 * min(1.0, value * 2)),
            round(255 * min(1.0, 2 - value * 2)), 0)


def colors_for_metrics(metrics, now):
    # Every accessory uses exactly this phase; hardware's autonomous effects
    # would drift, so send static frames instead.
    brightness = 0.72 + 0.28 * (0.5 + 0.5 * math.cos(2 * math.pi * now / PERIOD))
    loads = {"ram": metrics["ram_percent"], "gpu": metrics["gpu_percent"],
             "chassis": (metrics["cpu_celsius"] - 35.0) * 2.0}
    return {name: tuple(round(channel * brightness) for channel in color_for_load(load))
            for name, load in loads.items()}


def ram_usage(path=Path("/proc/meminfo")):
    values = {}
    for line in path.read_text().splitlines():
        key, value = line.split(":", 1)
        values[key] = int(value.split()[0])
    return 100.0 * (1 - values["MemAvailable"] / values["MemTotal"])


def cpu_sensor(root=Path("/sys/class/hwmon")):
    for hwmon in sorted(root.glob("hwmon*")):
        if (hwmon / "name").read_text().strip() == "k10temp":
            for label in hwmon.glob("temp*_label"):
                if label.read_text().strip() == "Tctl":
                    return label.with_name(label.name.replace("_label", "_input"))
    raise RuntimeError("AMD k10temp Tctl CPU sensor not found")


class Metrics:
    def __init__(self):
        self.cpu_path = cpu_sensor()

    def read(self):
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits", "-i", "0"],
            capture_output=True, text=True, check=True, timeout=3)
        return {"ram_percent": ram_usage(), "gpu_percent": float(result.stdout.strip()),
                "cpu_celsius": float(self.cpu_path.read_text()) / 1000.0}


def configure_chassis(device):
    """Populate every ARGB header to the driver's reported supported length.

    ASUS Direct mode skips zero-length zones. The installed driver reports
    120 LEDs per header; no physical chain length or EEPROM setting is assumed.
    """
    headers = [(index, zone.leds_max) for index, zone in enumerate(device.data.zones)
               if zone.name.startswith("Addressable RGB Header ")]
    if len(headers) != 3:
        raise RuntimeError(f"Expected three ASUS ARGB headers, found {len(headers)}")
    for index, maximum in headers:
        if not 1 <= maximum <= 120:
            raise RuntimeError(f"Unsupported ASUS header capacity: {maximum}")
        if len(device.zones[index].leds) != maximum:
            device.zones[index].resize(maximum)
    device.update()
    for index, maximum in headers:
        if len(device.zones[index].leds) != maximum:
            raise RuntimeError(f"ASUS header {index} resize was not confirmed")
    LOG.info("ASUS ARGB header lengths: %s", [len(device.zones[index].leds) for index, _ in headers])


def configure_direct(device):
    mode = next((mode for mode in device.modes if mode.name == 'Direct'), None)
    if mode is None:
        raise RuntimeError(f'{device.name} has no per-LED Direct mode')
    # Brightness is scaled in the shared frame; do not multiply it again by
    # the RAM controller's default 80% hardware brightness.
    if mode.brightness is not None and mode.brightness_max is not None:
        mode.brightness = mode.brightness_max
    device.set_mode(mode)


def gpu_progress_colors(count, fraction, color):
    """Fill from LED 0 towards the next PRL; leave unfilled LEDs black.

    Filled LEDs retain next-PRL brightness, with a proportionally dimmer
    boundary LED for fractions between whole LEDs.
    """
    fraction = max(0.0, min(1.0, fraction))
    return [tuple(round(channel * fraction * max(0.0, min(1.0, fraction * count - index)))
                  for channel in color)
            for index in range(count)]


def gpu_ripple_colors(count, fraction, color, now):
    """Sweep a full-brightness highlight only through the filled GPU bar."""
    colors = gpu_progress_colors(count, fraction, color)
    filled = min(count, math.ceil(max(0.0, min(1.0, fraction)) * count))
    phase = now % GPU_WAVE_PERIOD
    if not filled or phase >= GPU_WAVE_SWEEP:
        return colors
    # Let the tail leave the filled section before the next sweep begins.
    head = int(phase / GPU_WAVE_SWEEP * (filled + GPU_WAVE_TAIL))
    for index in range(filled):
        distance = head - index
        if 0 <= distance < GPU_WAVE_TAIL:
            intensity = (1 + math.cos(math.pi * distance / GPU_WAVE_TAIL)) / 2
            colors[index] = tuple(max(base, round(channel * intensity))
                                  for base, channel in zip(colors[index], color))
    return colors


class Hardware:
    def __init__(self, port):
        from openrgb import OpenRGBClient
        from openrgb.utils import RGBColor
        self.rgb_color = RGBColor
        self.client = OpenRGBClient(address="127.0.0.1", port=port, name="stellanor-rgb-stats")
        try:
            targets = {"ram": "Kingston Fury DDR5 DRAM", "gpu": "ASUS ROG ASTRAL GeForce RTX 5090 OC",
                       "chassis": "ASUS ROG STRIX X870-F GAMING WIFI"}
            # The server accepts connections before hardware detection finishes.
            # Wait for both controllers on a cold login rather than failing once.
            deadline = time.monotonic() + 20.0
            while True:
                self.devices = {key: [device for device in self.client.devices if device.name == name]
                                for key, name in targets.items()}
                if all(self.devices.values()) or time.monotonic() >= deadline:
                    break
                time.sleep(0.5)
                self.client.update()
            for key, devices in self.devices.items():
                if not devices:
                    raise RuntimeError(f"Missing {key} controller: {targets[key]}")
                for device in devices:
                    if key == "chassis":
                        configure_chassis(device)
                    configure_direct(device)
            # All directly detected accessories participate, including Razer.
            selected = {device.id for devices in self.devices.values() for device in devices}
            self.devices['accessories'] = [device for device in self.client.devices if device.id not in selected]
            for device in self.devices['accessories']:
                configure_direct(device)
            LOG.info('Synchronized controllers: %s', [d.name for group in self.devices.values() for d in group])
            self.previous = {}
            self.previous_progress = {}
        except BaseException:
            self.client.disconnect()
            raise

    def write(self, colors):
        for key, devices in self.devices.items():
            if colors[key] != self.previous.get(key):
                for device in devices:
                    device.set_color(self.rgb_color(*colors[key]), fast=True)
        self.previous = colors.copy()

    def write_progress(self, progress, now, stale=False, *, brightness=1.0):
        for group, devices in self.devices.items():
            for device in devices:
                pending = []
                for index, zone in enumerate(device.zones):
                    if zone.leds and zone.name != 'Void':
                        if group == 'gpu':
                            colors = gpu_ripple_colors(len(zone.leds), brightness,
                                                       self.theme.accent(progress, stale, now), now)
                        else:
                            colors = self.theme.progress_colors(len(zone.leds), progress, now, stale,
                                                               brightness=brightness)
                        key = (device.id, index)
                        if self.previous_progress.get(key) == colors:
                            continue
                        frame = [self.rgb_color(*color) for color in colors]
                        zone.set_colors(frame, fast=True)
                        pending.append((index, key, colors, frame))
                if pending:
                    # OpenRGB queues updates separately for each controller.
                    # Read back after writes to wait for slow RAM/USB queues,
                    # then avoid resending identical frames on every poll.
                    deadline = time.monotonic() + CONFIRM_TIMEOUT
                    while True:
                        device.update()
                        if all(device.zones[index].colors == frame
                               for index, _, _, frame in pending):
                            break
                        if time.monotonic() >= deadline:
                            raise RuntimeError(f'{device.name}: RGB frame was not confirmed')
                        time.sleep(.05)
                    for index, key, colors, frame in pending:
                        self.previous_progress[key] = colors

    def close(self):
        self.client.disconnect()


def load_theme(path):
    spec = importlib.util.spec_from_file_location('pearl_theme', path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Cannot load Pearl theme: {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_progress(path):
    try:
        state = json.loads(path.read_text())
        progress = float(state['payout_progress'])
        if not math.isfinite(progress):
            raise ValueError('invalid payout progress')
        # Reuse the dashboard's next-PRL fraction when available. Older status
        # writers expose the same total balance but not the derived fraction.
        brightness = state.get('next_prl_progress')
        if brightness is None:
            total = float(state['balance']['total'])
            if not math.isfinite(total) or total < 0:
                raise ValueError('invalid pool balance')
            brightness = total % 1.0
        brightness = float(brightness)
        if not math.isfinite(brightness) or not 0 <= brightness <= 1:
            raise ValueError('invalid next PRL progress')
        from datetime import datetime
        updated = state.get('pool_updated_at')
        stale = (not updated or time.time() - datetime.fromisoformat(updated).timestamp() > 600
                 or 'pool' in state.get('errors', {}))
        return progress, brightness, stale
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0, 0.0, True


def interpolated(before, after, elapsed):
    fraction = min(1.0, max(0.0, elapsed / SAMPLE_INTERVAL))
    return {key: before[key] + (after[key] - before[key]) * fraction for key in after}


def write_status(path, metrics, colors, now):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"updated_unix": time.time(), "animation_clock": now,
                                     "metrics": metrics, "colors": colors}, indent=2) + "\n")
    temporary.replace(path)


def notify_ready():
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
            notify.connect(address)
            notify.sendall(b"READY=1\nSTATUS=Pearl payout progress synchronized across RGB accessories")


def run(args):
    if args.metrics_only:
        print(json.dumps(Metrics().read(), indent=2))
        return
    theme = load_theme(args.pearl_theme)
    hardware = Hardware(args.port)
    hardware.theme = theme
    running = True

    def stop(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        ready = False
        while running:
            now = time.monotonic()
            progress, brightness, stale = read_progress(args.pearl_status)
            hardware.write_progress(progress, now, stale, brightness=brightness)
            if args.status_file:
                write_status(args.status_file, {'payout_progress': progress,
                             'next_prl_progress': brightness, 'brightness_percent': brightness * 100,
                             'stale': stale},
                             {'preview': theme.progress_colors(12, progress, now, stale,
                                                               brightness=brightness)}, now)
            if not ready:
                notify_ready()
                ready = True
            if args.once:
                return
            time.sleep(FRAME_INTERVAL)
    finally:
        hardware.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=6742)
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--metrics-only", action="store_true")
    parser.add_argument("--pearl-theme", type=Path, required=True)
    parser.add_argument("--pearl-status", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(args)


if __name__ == "__main__":
    main()
