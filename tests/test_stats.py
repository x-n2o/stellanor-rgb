import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('rgb_stats', Path(__file__).resolve().parents[1] / 'rgb-stats.py')
rgb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rgb)


class StatsTest(unittest.TestCase):
    def test_direct_mode_uses_full_hardware_brightness_before_frame_scaling(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        direct = SimpleNamespace(name='Direct', brightness=80, brightness_max=100)
        device = SimpleNamespace(name='RAM', modes=[direct], set_mode=Mock())
        rgb.configure_direct(device)
        self.assertEqual(direct.brightness, 100)
        device.set_mode.assert_called_once_with(direct)
        direct.brightness = direct.brightness_max = None
        rgb.configure_direct(device)
        self.assertIsNone(direct.brightness)

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
            self.assertEqual(rgb.read_progress(path), (0, 0, True))
            state = {'payout_progress': 0.39, 'balance': {'total': 2.25},
                     'pool_updated_at': datetime.now().isoformat(), 'errors': {}}
            path.write_text(json.dumps(state))
            self.assertEqual(rgb.read_progress(path), (0.39, .25, False))
            state['errors'] = {'pool': 'offline'}
            path.write_text(json.dumps(state))
            self.assertEqual(rgb.read_progress(path), (0.39, .25, True))

    def test_next_prl_brightness_resets_at_each_whole_coin(self):
        import json
        from datetime import datetime
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pearl.json'
            for total, expected in [(0, 0), (2.25, .25), (2.99, .99), (3, 0), (3.5, .5)]:
                path.write_text(json.dumps({'payout_progress': total,
                    'balance': {'total': total}, 'pool_updated_at': datetime.now().isoformat()}))
                progress, brightness, stale = rgb.read_progress(path)
                self.assertEqual(progress, total)
                self.assertAlmostEqual(brightness, expected)
                self.assertFalse(stale)

    def test_published_next_prl_fraction_is_preferred_and_validated(self):
        import json
        from datetime import datetime
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pearl.json'
            state = {'payout_progress': 2.9, 'balance': {'total': 2.9},
                     'pool_updated_at': datetime.now().isoformat(), 'next_prl_progress': .5}
            path.write_text(json.dumps(state))
            self.assertEqual(rgb.read_progress(path), (2.9, .5, False))
            for value in [float('nan'), float('inf'), -.1, 1.1, 'invalid']:
                state['next_prl_progress'] = value
                path.write_text(json.dumps(state))
                self.assertEqual(rgb.read_progress(path), (0, 0, True))

    def test_gpu_bar_fill_brightness_and_coin_rollover(self):
        color = (100, 200, 60)
        self.assertEqual(rgb.gpu_progress_colors(30, 0, color), [(0, 0, 0)] * 30)
        self.assertEqual(rgb.gpu_progress_colors(30, .5, color),
                         [(50, 100, 30)] * 15 + [(0, 0, 0)] * 15)
        self.assertEqual(rgb.gpu_progress_colors(30, 1, color), [color] * 30)
        self.assertEqual(rgb.gpu_progress_colors(30, .09, color),
                         [(9, 18, 5)] * 2 + [(6, 13, 4)] + [(0, 0, 0)] * 27)
        self.assertEqual(rgb.gpu_progress_colors(0, .5, color), [])

    def test_gpu_fill_uses_next_prl_fraction_and_shared_status_colour(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        def accent(progress, stale, now):
            self.assertEqual((progress, now), (3.5, 7))
            return (255, 145, 24) if stale else (100, 255, 60)
        theme = SimpleNamespace(accent=accent, progress_colors=Mock(return_value=[(4, 5, 6)] * 30))
        class Zone:
            name = 'Unknown'
            leds = [None] * 30
            def set_colors(self, colors, fast=False):
                self.colors = colors
        gpu, ram = Zone(), Zone()
        hardware = rgb.Hardware.__new__(rgb.Hardware)
        hardware.devices = {
            'gpu': [SimpleNamespace(id=0, zones=[gpu], update=lambda: None)],
            'ram': [SimpleNamespace(id=1, zones=[ram], update=lambda: None)],
        }
        hardware.previous_progress = {}
        hardware.theme = theme
        hardware.rgb_color = lambda *channels: channels
        for stale in [False, True]:
            hardware.write_progress(3.5, 7, stale, brightness=.5)
            base = theme.accent(3.5, stale, 7)
            self.assertEqual(gpu.colors, [tuple(round(channel * .5) for channel in base)] * 15
                             + [(0, 0, 0)] * 15)
            self.assertEqual(ram.colors, [(4, 5, 6)] * 30)
            theme.progress_colors.assert_called_with(30, 3.5, 7, stale, brightness=.5)
        hardware.write_progress(3.5, 7, brightness=0)
        self.assertEqual(gpu.colors, [(0, 0, 0)] * 30)

    def test_all_hardware_zones_receive_the_same_next_prl_brightness(self):
        from types import SimpleNamespace
        calls = []
        class Zone:
            name = 'Keyboard'
            leds = [None] * 4
            def set_colors(self, colors, fast=False):
                calls.append((colors, fast))
                self.colors = colors
        hardware = rgb.Hardware.__new__(rgb.Hardware)
        updates = []
        hardware.devices = {'accessories': [SimpleNamespace(id=0, zones=[Zone(), Zone()],
                                                          update=lambda: updates.append(True))]}
        hardware.previous_progress = {}
        def colors(count, progress, now, stale, *, brightness):
            self.assertEqual((count, progress, now, stale, brightness), (4, 2.5, 7, False, .5))
            return [(50, 128, 30)] * count
        hardware.theme = SimpleNamespace(progress_colors=colors)
        hardware.rgb_color = lambda *channels: channels
        hardware.write_progress(2.5, 7, brightness=.5)
        hardware.write_progress(2.5, 7, brightness=.5)
        self.assertEqual(calls, [([(50, 128, 30)] * 4, True)] * 2)
        self.assertEqual(updates, [True])

    def test_unconfirmed_device_frame_is_not_cached(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        zone = SimpleNamespace(name='RAM', leds=[None], colors=[(0, 0, 0)],
                               set_colors=lambda *args, **kwargs: None)
        hardware = rgb.Hardware.__new__(rgb.Hardware)
        hardware.devices = {'ram': [SimpleNamespace(id=0, name='RAM', zones=[zone], update=lambda: None)]}
        hardware.previous_progress = {}
        hardware.theme = SimpleNamespace(progress_colors=lambda *args, **kwargs: [(50, 128, 30)])
        hardware.rgb_color = lambda *channels: channels
        with patch.object(rgb.time, 'monotonic', side_effect=[0, 11]):
            with self.assertRaisesRegex(RuntimeError, 'RGB frame was not confirmed'):
                hardware.write_progress(2.5, 7, brightness=.5)
        self.assertEqual(hardware.previous_progress, {})

    def test_slow_controller_is_polled_without_resending_the_frame(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        zone = SimpleNamespace(name='RAM', leds=[None], colors=[(0, 0, 0)], set_colors=Mock())
        updates = []
        def update():
            updates.append(True)
            if len(updates) == 2:
                zone.colors = [(50, 128, 30)]
        hardware = rgb.Hardware.__new__(rgb.Hardware)
        hardware.devices = {'ram': [SimpleNamespace(id=0, name='RAM', zones=[zone], update=update)]}
        hardware.previous_progress = {}
        hardware.theme = SimpleNamespace(progress_colors=lambda *args, **kwargs: [(50, 128, 30)])
        hardware.rgb_color = lambda *channels: channels
        with patch.object(rgb.time, 'sleep'):
            hardware.write_progress(2.5, 7, brightness=.5)
        self.assertEqual(len(updates), 2)
        zone.set_colors.assert_called_once()
        self.assertEqual(hardware.previous_progress, {(0, 0): [(50, 128, 30)]})
