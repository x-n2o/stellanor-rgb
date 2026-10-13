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
        systemctl = bindir / 'systemctl'
        systemctl.write_text("""#!/bin/sh
echo "$*" >> "$HOME/systemctl-calls"
case "$*" in
  *"is-active"*)
    if test "$*" = "--user is-active --quiet pearl-watch-trcc.service" && test -f "$HOME/pearl-active"; then exit 0; fi
    exit 3;;
  *"LoadState"*)
    if test -f "$HOME/services"; then echo loaded; else echo not-found; fi;;
esac
exit 0
""")
        systemctl.chmod(0o755)
        trcc = bindir / 'trcc'
        trcc.write_text('#!/bin/sh\necho "$*" >> "$HOME/trcc-calls"\n')
        trcc.chmod(0o755)
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

    def test_stats_requires_installed_service(self):
        self.assertNotEqual(self.run_lights('stats').returncode, 0)
        self.assertFalse((self.root / 'calls').exists())

    def test_stats_does_not_touch_lcd_or_oneshot_rgb(self):
        (self.root / 'services').touch()
        result = self.run_lights('stats')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'Lights stats')
        self.assertFalse((self.root / 'calls').exists())
        self.assertFalse((self.root / 'trcc-calls').exists())
        self.assertEqual(self.run_lights().stdout.strip(), 'Lights off')

    def test_trcc_off_stops_pearl_before_sleep(self):
        (self.root / 'services').touch()
        self.assertEqual(self.run_lights('off').returncode, 0)
        calls = (self.root / 'systemctl-calls').read_text()
        self.assertIn('--user stop pearl-watch-trcc.service', calls)
        self.assertEqual((self.root / 'trcc-calls').read_text(), 'display sleep 87ad:70db\n')

    def test_trcc_on_starts_pearl_without_legacy_reset(self):
        (self.root / 'services').touch()
        self.assertEqual(self.run_lights('on').returncode, 0)
        calls = (self.root / 'systemctl-calls').read_text()
        self.assertIn('--user start pearl-watch-trcc.service', calls)
        self.assertLess(calls.index('stop stellanor-display-blank.service'),
                        calls.index('--user start pearl-watch-trcc.service'))
        self.assertLess(calls.index('--user stop trcc-tps.service'),
                        calls.index('--user start pearl-watch-trcc.service'))
        self.assertFalse((self.root / 'trcc-calls').exists())

    def test_toggle_turns_off_active_pearl_despite_saved_off(self):
        (self.root / 'services').touch()
        state = self.root / 'state/chassis-lights'
        state.mkdir(parents=True)
        (state / 'state').write_text('off\n')
        (self.root / 'pearl-active').touch()
        result = self.run_lights()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'Lights off')
        self.assertIn('--user stop pearl-watch-trcc.service',
                      (self.root / 'systemctl-calls').read_text())
        self.assertEqual((self.root / 'trcc-calls').read_text(),
                         'display sleep 87ad:70db\n')
