import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class LightsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copy(Path(__file__).resolve().parents[1] / 'lights.sh', self.root)
        (self.root / 'thermalright-display.py').write_text('pass\n')
        bindir = self.root / 'bin'
        bindir.mkdir()
        sudo = bindir / 'sudo'
        sudo.write_text('#!/bin/sh\nexec "$@"\n')
        sudo.chmod(0o755)
        liquidctl = bindir / 'liquidctl'
        liquidctl.write_text('#!/bin/sh\nexit 0\n')
        liquidctl.chmod(0o755)
        binary = self.root / 'openrgb/squashfs-root/AppRun'
        binary.parent.mkdir(parents=True)
        binary.write_text('''#!/bin/sh
echo "$*" >> "$HOME/calls"
case " $* " in
  *" --list-devices "*)
    echo '0: Kingston Fury DDR5 DRAM'
    echo '1: ASUS ROG ASTRAL GeForce RTX 5090 OC'
    test -f "$HOME/missing" || echo '2: ASUS ROG STRIX X870-F GAMING WIFI'
    exit 0;;
esac
test ! -f "$HOME/fail"
''')
        binary.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.root),
                        XDG_STATE_HOME=str(self.root / 'state'),
                        LIGHTS_OPENRGB_DIR=str(self.root / 'openrgb'),
                        PATH=str(bindir) + ':' + os.environ['PATH'])

    def run_lights(self, *args):
        return subprocess.run(['bash', str(self.root / 'lights.sh'), *args],
                              env=self.env, capture_output=True, text=True)

    def test_off_then_toggle_twice(self):
        for args, expected in [(('off',), 'off'), ((), 'on'), ((), 'off')]:
            result = self.run_lights(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'Lights ' + expected)
        calls = (self.root / 'calls').read_text().splitlines()
        self.assertEqual(len(calls), 6)
        for command in calls[1::2]:
            self.assertNotIn('--list-devices', command)
            self.assertIn('--mode', command)

    def test_failed_write_preserves_state(self):
        self.assertEqual(self.run_lights('off').returncode, 0)
        (self.root / 'fail').touch()
        self.assertNotEqual(self.run_lights('on').returncode, 0)
        self.assertEqual((self.root / 'state/chassis-lights/state').read_text(), 'off\n')

    def test_missing_controller_prevents_write(self):
        (self.root / 'missing').touch()
        self.assertNotEqual(self.run_lights('off').returncode, 0)
        self.assertEqual(len((self.root / 'calls').read_text().splitlines()), 1)

    def test_invalid_argument_does_not_touch_hardware(self):
        self.assertEqual(self.run_lights('invalid').returncode, 2)
        self.assertFalse((self.root / 'calls').exists())
