import importlib.util
import io
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from PIL import Image

spec = importlib.util.spec_from_file_location(
    'display', Path(__file__).resolve().parents[1] / 'thermalright-display.py')
display = importlib.util.module_from_spec(spec)
spec.loader.exec_module(display)


class DisplayTest(unittest.TestCase):
    def test_black_frame_matches_wire_header(self):
        frame = display.black_frame()
        self.assertEqual(frame[:4], bytes.fromhex('12345678'))
        self.assertEqual(struct.unpack_from('<III', frame, 4), (2, 640, 480))
        self.assertEqual(struct.unpack_from('<II', frame, 56), (2, len(frame) - 64))
        with Image.open(io.BytesIO(frame[64:])) as image:
            self.assertEqual(image.size, (640, 480))
            self.assertEqual(image.getextrema(), ((0, 0), (0, 0), (0, 0)))

    def test_off_starts_service_and_waits_for_ready(self):
        with patch.object(display.sys, 'argv', ['display', 'off']), \
                patch.object(display.subprocess, 'run') as run:
            run.return_value.stdout = 'not-found\n'
            display.main()
            command = run.call_args_list[-1].args[0]
            self.assertEqual(command[0], 'systemd-run')
            self.assertIn('--property=Type=notify', command)
            self.assertEqual(command[-1], 'stream')

    def test_on_stops_stream_before_reset(self):
        order = []
        with patch.object(display.sys, 'argv', ['display', 'on']), \
                patch.object(display.subprocess, 'run') as run, \
                patch.object(display, 'display', side_effect=lambda action: order.append(action)):
            def command(args, **kwargs):
                order.append(args[:2])
                from types import SimpleNamespace
                return SimpleNamespace(stdout='loaded\n')
            run.side_effect = command
            display.main()
        self.assertEqual(order[-2:], [['systemctl', 'stop'], 'on'])
