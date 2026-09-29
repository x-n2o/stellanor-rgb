#!/usr/bin/python3
"""Blank the local ChiZhu LCD; reset it to its built-in logo for on.

Protocol reference: rejeb/thermalright-lcd-control, display/usb_devices.py
and display/device_probe.py. This only addresses USB display 87ad:70db.
"""
import io
import struct
import sys
import time

import usb.core
import usb.util
from PIL import Image

import os
from pathlib import Path
import socket
import subprocess

UNIT = "stellanor-display-blank.service"


def black_frame():
    """Build the 640x480 JPEG accepted by this panel."""
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), "black").save(
        buf, format="JPEG", quality=95, subsampling=0)
    payload = buf.getvalue()
    header = bytearray(64)
    header[:4] = bytes.fromhex("12345678")
    struct.pack_into("<III", header, 4, 2, 640, 480)
    struct.pack_into("<II", header, 56, 2, len(payload))
    return bytes(header) + payload


def notify_ready():
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(b"READY=1")


def display(action):
    dev = usb.core.find(idVendor=0x87AD, idProduct=0x70DB)
    if dev is None:
        raise RuntimeError("Thermalright USB display not found")
    try:
        if action == "on":
            dev.reset()
            return
        dev.set_configuration()
        interface = dev.get_active_configuration()[(0, 0)]
        out_ep = next(e for e in interface if not e.bEndpointAddress & 0x80)
        in_ep = next(e for e in interface if e.bEndpointAddress & 0x80)
        usb.util.claim_interface(dev, 0)
        dev.write(out_ep.bEndpointAddress,
                  bytes.fromhex("12345678") + bytes(52) + b"\x01" + bytes(7),
                  timeout=3000)
        response = bytes(dev.read(in_ep.bEndpointAddress, 1024, timeout=3000))
        if len(response) < 41 or response[32] != 7:
            raise RuntimeError("Unexpected Thermalright panel; refusing wrong frame size")
        time.sleep(2)
        frame = black_frame()
        ready = False
        while True:
            for offset in range(0, len(frame), 16384):
                chunk = frame[offset:offset + 16384]
                if dev.write(out_ep.bEndpointAddress, chunk, timeout=5000) != len(chunk):
                    raise RuntimeError("Incomplete display frame write")
            if len(frame) % 512 == 0:
                dev.write(out_ep.bEndpointAddress, b"", timeout=1000)
            if not ready:
                notify_ready()
                ready = True
            time.sleep(0.1)
    finally:
        usb.util.dispose_resources(dev)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("on", "off", "stream"):
        raise SystemExit("Expected on or off")
    action = sys.argv[1]
    if action == "stream":
        display(action)
        return
    # Stop any previous stream before resetting or starting a new one.
    state = subprocess.run(
        ["systemctl", "show", UNIT, "--property=LoadState", "--value"],
        check=True, capture_output=True, text=True).stdout.strip()
    if state != "not-found":
        subprocess.run(["systemctl", "stop", UNIT], check=True)
    if action == "on":
        display(action)
    else:
        # Type=notify waits for the first successful frame before reporting success.
        subprocess.run([
            "systemd-run", "--quiet", "--collect", "--unit=" + UNIT,
            "--property=Type=notify", "--property=TimeoutStartSec=15",
            "--property=Restart=on-failure", "--property=RestartSec=2",
            "/usr/bin/python3", str(Path(__file__).resolve()), "stream",
        ], check=True)


if __name__ == "__main__":
    main()
