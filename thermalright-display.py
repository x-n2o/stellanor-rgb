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

action = sys.argv[1]
if action not in ("on", "off"):
    raise SystemExit("Expected on or off")
dev = usb.core.find(idVendor=0x87AD, idProduct=0x70DB)
if dev is None:
    raise SystemExit("Thermalright USB display not found")
try:
    if action == "on":
        dev.reset()
    else:
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
        width, height = 640, 480
        buf = io.BytesIO()
        Image.new("RGB", (width, height), "black").save(buf, format="JPEG", quality=95, subsampling=0)
        payload = buf.getvalue()
        header = bytearray(64)
        header[:4] = bytes.fromhex("12345678")
        struct.pack_into("<III", header, 4, 2, width, height)
        struct.pack_into("<II", header, 56, 2, len(payload))
        frame = bytes(header) + payload
        for _ in range(5):
            for offset in range(0, len(frame), 16384):
                chunk = frame[offset:offset + 16384]
                if dev.write(out_ep.bEndpointAddress, chunk, timeout=5000) != len(chunk):
                    raise RuntimeError("Incomplete display frame write")
            if len(frame) % 512 == 0:
                dev.write(out_ep.bEndpointAddress, b"", timeout=1000)
            time.sleep(0.1)

finally:
    usb.util.dispose_resources(dev)
