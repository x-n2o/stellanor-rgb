import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('rgb_stats', Path(__file__).resolve().parents[1] / 'rgb-stats.py')
rgb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rgb)


class StatsTest(unittest.TestCase):
    def test_gradient_landmarks_and_clamping(self):
        for load, expected in [(-1, (0, 255, 0)), (0, (0, 255, 0)),
                               (50, (255, 255, 0)), (100, (255, 0, 0)),
                               (101, (255, 0, 0))]:
            self.assertEqual(rgb.color_for_load(load), expected)

    def test_cpu_range_and_synchronized_animation(self):
        for temperature, load in [(35, 0), (60, 50), (85, 100)]:
            metrics = dict(ram_percent=load, gpu_percent=load, cpu_celsius=temperature)
            for now in [0, .5, 1, 2, 3, 4]:
                colors = rgb.colors_for_metrics(metrics, now)
                self.assertEqual(colors['ram'], colors['gpu'])
                self.assertEqual(colors['gpu'], colors['chassis'])
        metrics = dict(ram_percent=100, gpu_percent=100, cpu_celsius=85)
        self.assertEqual(rgb.colors_for_metrics(metrics, 0)['ram'], (255, 0, 0))
        self.assertEqual(rgb.colors_for_metrics(metrics, 2)['ram'], (184, 0, 0))

    def test_ram_uses_available_instead_of_free(self):
        with tempfile.TemporaryDirectory() as directory:
            meminfo = Path(directory) / 'meminfo'
            meminfo.write_text('MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 750 kB\n')
            self.assertEqual(rgb.ram_usage(meminfo), 25)

    def test_interpolation_smooth_and_bounded(self):
        before, after = {'gpu_percent': 0}, {'gpu_percent': 100}
        for elapsed, value in [(-1, 0), (0, 0), (.5, 50), (1, 100), (2, 100)]:
            self.assertEqual(rgb.interpolated(before, after, elapsed)['gpu_percent'], value)

    def test_cpu_sensor_uses_k10temp_tctl(self):
        with tempfile.TemporaryDirectory() as directory:
            hwmon = Path(directory) / 'hwmon9'
            hwmon.mkdir()
            (hwmon / 'name').write_text('k10temp\n')
            (hwmon / 'temp1_label').write_text('Tctl\n')
            (hwmon / 'temp1_input').write_text('55000\n')
            self.assertEqual(rgb.cpu_sensor(Path(directory)), hwmon / 'temp1_input')

    def test_status_is_valid_json_and_atomic_temp_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stats.json'
            rgb.write_status(path, {'ram_percent': 10}, {'ram': (0, 255, 0)}, 10)
            self.assertFalse(path.with_suffix('.tmp').exists())
            self.assertIn('"ram_percent": 10', path.read_text())

    def test_hardware_writes_all_three_accessories_and_skips_duplicates(self):
        class Device:
            def __init__(self):
                self.calls = []
            def set_color(self, color, fast=False):
                self.calls.append((color, fast))
        hardware = rgb.Hardware.__new__(rgb.Hardware)
        ram, gpu, chassis = Device(), Device(), Device()
        hardware.devices = {'ram': [ram], 'gpu': [gpu], 'chassis': [chassis]}
        hardware.rgb_color = lambda *channels: channels
        hardware.previous = {}
        colors = {'ram': (10, 255, 0), 'gpu': (255, 0, 0), 'chassis': (255, 200, 0)}
        hardware.write(colors)
        hardware.write(colors)
        for name, device in [('ram', ram), ('gpu', gpu), ('chassis', chassis)]:
            self.assertEqual(device.calls, [(colors[name], True)])

    def test_three_fixed_leds_and_all_three_empty_headers_are_populated(self):
        from types import SimpleNamespace
        class Zone:
            def __init__(self, size):
                self.leds = [None] * size
                self.resizes = []
            def resize(self, size):
                self.resizes.append(size)
                self.leds = [None] * size
        device = SimpleNamespace(
            zones=[Zone(3), Zone(0), Zone(0), Zone(0)], update=lambda: None,
            data=SimpleNamespace(zones=[SimpleNamespace(name='Aura Mainboard', leds_max=3)] +
                [SimpleNamespace(name=f'Addressable RGB Header {index}', leds_max=120)
                 for index in [1, 2, 3]]))
        rgb.configure_chassis(device)
        rgb.configure_chassis(device)
        self.assertEqual([len(zone.leds) for zone in device.zones], [3, 120, 120, 120])
        self.assertEqual([zone.resizes for zone in device.zones], [[], [120], [120], [120]])
        device.data.zones[1].leds_max = 121
        with self.assertRaisesRegex(RuntimeError, 'Unsupported ASUS header capacity'):
            rgb.configure_chassis(device)

    def test_theme_is_loaded_from_shared_module(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pearl_theme.py'
            path.write_text('def progress_colors(*args): return args\n')
            theme = rgb.load_theme(path)
            self.assertEqual(theme.progress_colors(4, .5, 1, True), (4, .5, 1, True))

    def test_shared_pearl_snapshot_and_stale_failure(self):
        import json
        from datetime import datetime
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pearl.json'
            self.assertEqual(rgb.read_progress(path), (0, True))
            state = {'payout_progress': 0.39, 'pool_updated_at': datetime.now().isoformat(), 'errors': {}}
            path.write_text(json.dumps(state))
            self.assertEqual(rgb.read_progress(path), (0.39, False))
            state['errors'] = {'pool': 'offline'}
            path.write_text(json.dumps(state))
            self.assertEqual(rgb.read_progress(path), (0.39, True))
