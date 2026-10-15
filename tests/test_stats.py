import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('rgb_stats', Path(__file__).resolve().parents[1] / 'rgb-stats.py')
rgb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rgb)


def linear_device(device_id, zones):
    from types import SimpleNamespace
    from unittest.mock import Mock
    data = []
    offset = 0
    for name, size in zones:
        data.append(SimpleNamespace(name=name, start_idx=offset, num_leds=size))
        offset += size
    device = SimpleNamespace(id=device_id, name='Linear device', leds=[None] * offset,
        colors=[(0, 0, 0)] * offset, data=SimpleNamespace(zones=data),
        zones=[SimpleNamespace(name=zone.name, leds=[None] * zone.num_leds, set_colors=Mock())
               for zone in data], update=Mock())
    def set_colors(colors, fast=False):
        device.colors = colors
    device.set_colors = Mock(side_effect=set_colors)
    return device


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
            self.assertEqual((progress, now), (3.5, 1.5))
            return (255, 145, 24) if stale else (100, 255, 60)
        theme = SimpleNamespace(accent=accent, progress_colors=Mock(return_value=[(4, 5, 6)] * 30))
        class Zone:
            name = 'Unknown'
            leds = [None] * 30
            def set_colors(self, colors, fast=False):
                self.colors = colors
        gpu, ram = Zone(), linear_device(1, [('RAM', 30)])
        hardware = rgb.Hardware.__new__(rgb.Hardware)
        hardware.devices = {
            'gpu': [SimpleNamespace(id=0, zones=[gpu], update=lambda: None)],
            'ram': [ram],
        }
        hardware.previous_progress = {}
        hardware.ripple_cycle = rgb.GPURippleCycle()
        hardware.ripple_cycle.phase(.5, 0)
        hardware.reveal_cycle = rgb.RevealRippleCycle()
        hardware.reveal_cycle.phase(.5, 0)
        hardware.reveal_cycle.phase(.5, .2)
        hardware.theme = theme
        hardware.rgb_color = lambda *channels: channels
        for stale in [False, True]:
            hardware.write_progress(3.5, 1.5, stale, brightness=.5)
            base = theme.accent(3.5, stale, 1.5)
            self.assertEqual(gpu.colors, [tuple(round(channel * .5) for channel in base)] * 15
                             + [(0, 0, 0)] * 15)
            self.assertEqual(ram.colors, [tuple(round(channel * .5) for channel in base)] * 30)
            theme.progress_colors.assert_not_called()
        hardware.write_progress(3.5, 1.5, brightness=0)
        self.assertEqual(gpu.colors, [(0, 0, 0)] * 30)

    def test_gpu_ripple_moves_right_at_full_brightness_with_a_fading_tail(self):
        color = (100, 255, 60)
        first = rgb.ripple_colors(30, .5, color, 0)
        middle = rgb.ripple_colors(30, .5, color, 1 / 3)
        later = rgb.ripple_colors(30, .5, color, 2 / 3)
        self.assertEqual(first[0], color)
        self.assertEqual(middle[6], color)
        self.assertEqual(later[12], color)
        self.assertEqual(middle[5], (75, 191, 45))
        self.assertEqual(middle[0], (50, 128, 30))
        for frame in [first, middle, later]:
            self.assertEqual(frame[15:], [(0, 0, 0)] * 15)
        self.assertEqual(rgb.ripple_colors(30, .5, color, 1.5),
                         rgb.gpu_progress_colors(30, .5, color))
        cycle = rgb.GPURippleCycle()
        cycle.phase(.5, 0)
        self.assertEqual(rgb.ripple_colors(30, .5, color, cycle.phase(.5, 2.15)), first)

    def test_gpu_ripple_preserves_empty_bar_and_fractional_boundary(self):
        color = (100, 255, 60)
        for now in [0, .5, 1, 2, 3, 4]:
            self.assertEqual(rgb.ripple_colors(30, 0, color, now), [(0, 0, 0)] * 30)
            self.assertEqual(rgb.ripple_colors(0, .5, color, now), [])
            frame = rgb.ripple_colors(30, .09, color, now)
            self.assertEqual(frame[3:], [(0, 0, 0)] * 27)
        self.assertEqual(rgb.ripple_colors(30, .09, color, 1 / 3)[2], color)

    def test_gpu_ripple_pause_decreases_with_progress(self):
        color = (100, 255, 60)
        for fraction in [.01, .25, .5, .99, 1]:
            period = 1 + 2 - 1.7 * fraction
            base = rgb.gpu_progress_colors(30, fraction, color)
            cycle = rgb.GPURippleCycle()
            cycle.phase(fraction, 0)
            self.assertEqual(rgb.ripple_colors(30, fraction, color, cycle.phase(fraction, 1.01)), base)
            self.assertEqual(rgb.ripple_colors(30, fraction, color,
                                                 cycle.phase(fraction, period - .001)), base)
            self.assertEqual(rgb.ripple_colors(30, fraction, color,
                                                 cycle.phase(fraction, period + .001))[0], color)

    def test_fractional_balance_noise_does_not_light_a_fourth_gpu_led(self):
        import json
        from datetime import datetime
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pearl.json'
            path.write_text(json.dumps({'payout_progress': 2.1, 'balance': {'total': 2.1},
                                        'pool_updated_at': datetime.now().isoformat()}))
            _, fraction, _ = rgb.read_progress(path)
            self.assertEqual(rgb.gpu_fill_length(30, fraction), 3)
            for phase in (0, .25, .5, .75):
                self.assertEqual(rgb.ripple_colors(30, fraction, (100, 255, 60), phase)[3:],
                                 [(0, 0, 0)] * 27)

    def test_progress_update_does_not_jump_the_gpu_ripple_backwards(self):
        cycle = rgb.GPURippleCycle()
        cycle.phase(.49, 216700)
        color = (100, 255, 60)
        before = rgb.ripple_colors(30, .49, color, cycle.phase(.49, 216700.6),
                                       cycle_fraction=cycle.fraction)
        after = rgb.ripple_colors(30, .49001, color, cycle.phase(.49001, 216700.65),
                                      cycle_fraction=cycle.fraction)
        self.assertEqual(before.index(color), 10)
        self.assertEqual(after.index(color), 11)
        self.assertEqual(cycle.started_at, 216700)

    def test_progress_update_preserves_pause_until_the_next_cycle(self):
        cycle = rgb.GPURippleCycle()
        cycle.phase(.25, 100)
        period = cycle.period
        self.assertAlmostEqual(cycle.phase(.99, 101.5), 1.5)
        self.assertEqual(cycle.period, period)
        self.assertEqual(cycle.fraction, .25)
        self.assertAlmostEqual(cycle.phase(.99, 100 + period + .001), .001)
        self.assertAlmostEqual(cycle.period, 1.317)
        self.assertEqual(cycle.fraction, .99)
        self.assertEqual(cycle.phase(0, 103), 0)
        self.assertEqual(cycle.fraction, 0)

    def test_accessory_background_stays_lit_but_ripple_stops_at_progress(self):
        color = (100, 255, 60)
        self.assertEqual(rgb.ripple_colors(7, .1, color, 1.5, keep_lit=True),
                         [(10, 26, 6)] * 7)
        self.assertEqual(rgb.ripple_colors(7, 0, color, 1.5, keep_lit=True),
                         [(0, 0, 0)] * 7)
        self.assertEqual(rgb.ripple_colors(7, 0, color, 0, keep_lit=True), [(0, 0, 0)] * 7)
        self.assertEqual(rgb.ripple_colors(7, .1, color, 0, keep_lit=True)[0], color)
        for now in [0, .1, .3, .6, .99, 1.5]:
            self.assertEqual(rgb.ripple_colors(7, .1, color, now, keep_lit=True)[1:],
                             [(10, 26, 6)] * 6)
        self.assertEqual(rgb.ripple_colors(7, .5, color, 3 / 7, keep_lit=True)[3], color)
        cycle = rgb.GPURippleCycle()
        cycle.phase(.1, 0)
        self.assertEqual(rgb.ripple_colors(7, .1, color, cycle.phase(.1, 2.831), keep_lit=True)[0], color)

    def test_reveal_starts_black_and_leaves_only_the_passed_bar_lit(self):
        color = (100, 200, 60)
        self.assertEqual(rgb.ripple_colors(10, .5, color, 0, reveal=True), [(0, 0, 0)] * 10)
        first = rgb.ripple_colors(10, .5, color, .12, reveal=True)
        self.assertEqual(first, [color] + [(0, 0, 0)] * 9)
        middle = rgb.ripple_colors(10, .5, color, .34, reveal=True)
        self.assertEqual(middle, [(50, 100, 30), (75, 150, 45), color] + [(0, 0, 0)] * 7)
        self.assertEqual(rgb.ripple_colors(10, .5, color, 1.5, reveal=True),
                         [(50, 100, 30)] * 5 + [(0, 0, 0)] * 5)
        cycle = rgb.GPURippleCycle()
        cycle.phase(.5, 0)
        self.assertEqual(rgb.ripple_colors(10, .5, color, cycle.phase(.5, 2.15), reveal=True),
                         [(0, 0, 0)] * 10)

    def test_reveal_preserves_zero_full_and_fractional_boundaries(self):
        color = (100, 200, 60)
        for fraction in [0, .09, .5, 1]:
            self.assertEqual(rgb.ripple_colors(30, fraction, color, 0, reveal=True), [(0, 0, 0)] * 30)
            self.assertEqual(rgb.ripple_colors(30, fraction, color, 1.5, reveal=True),
                             rgb.gpu_progress_colors(30, fraction, color))
        for phase in [0, .2, .5, .8, 1.5]:
            self.assertEqual(rgb.ripple_colors(30, .10000000000000009, color, phase, reveal=True)[3:],
                             [(0, 0, 0)] * 27)

    def test_reveal_does_not_preilluminate_new_progress_before_the_next_cycle(self):
        color = (100, 200, 60)
        frame = rgb.ripple_colors(10, .8, color, .5, reveal=True, cycle_fraction=.2)
        self.assertEqual(frame[2:], [(0, 0, 0)] * 8)
        settled = rgb.ripple_colors(10, .8, color, 1.5, reveal=True, cycle_fraction=.2)
        self.assertEqual(settled, [(80, 160, 48)] * 2 + [(0, 0, 0)] * 8)
        self.assertEqual(rgb.ripple_colors(10, .1, color, 1.5, reveal=True, cycle_fraction=0),
                         [(0, 0, 0)] * 10)

    def test_delayed_updates_show_a_bright_reveal_between_black_resets(self):
        from types import SimpleNamespace
        color = (255, 0, 0)
        for interval in (.6, 2.2):
            with self.subTest(interval=interval):
                gpu = linear_device(0, [('GPU', 30)])
                accessories = [linear_device(1, [('Keyboard', 26)]),
                               linear_device(2, [('Mouse', 6)])]
                ram = linear_device(3, [('RAM', 12)])
                hardware = rgb.Hardware.__new__(rgb.Hardware)
                hardware.devices = {'gpu': [gpu], 'accessories': accessories, 'ram': [ram]}
                hardware.accessory_layouts = {d.id: (len(d.leds), list(range(len(d.leds))))
                                              for d in accessories}
                hardware.previous_progress = {}
                hardware.rgb_color = lambda *channels: channels
                hardware.theme = SimpleNamespace(accent=lambda *args: color)
                for zone in gpu.zones:
                    zone.colors = []
                    zone.set_colors.side_effect = lambda frame, fast=False, z=zone: setattr(z, 'colors', frame)
                seen_peak = [False] * 3
                resets = 0
                ram_cycle = rgb.GPURippleCycle()
                for tick in range(16):
                    now = round(tick * interval, 6)
                    hardware.write_progress(.5, now, brightness=.5)
                    frames = [gpu.zones[0].colors, *(device.colors for device in accessories)]
                    if not any(any(pixel) for pixel in frames[0]):
                        if tick:
                            self.assertTrue(all(seen_peak))
                        resets += 1
                        seen_peak = [False] * 3
                        self.assertTrue(all(not any(any(pixel) for pixel in frame) for frame in frames))
                    for index, frame in enumerate(frames):
                        seen_peak[index] |= color in frame
                        self.assertEqual(frame[len(frame)//2:], [(0, 0, 0)] * (len(frame)//2))
                    phase = ram_cycle.phase(.5, now)
                    self.assertEqual(ram.colors, rgb.linear_device_ripple_colors(
                        ram, .5, color, phase, cycle_fraction=ram_cycle.fraction))
                self.assertGreater(resets, 1)

    def test_keyboard_layout_uses_columns_and_the_wrist_rest_perimeter(self):
        from types import SimpleNamespace
        def zone(name, start, size, matrix=None):
            return SimpleNamespace(name=name, start_idx=start, num_leds=size, matrix_map=matrix)
        zones = [zone('Keyboard', 0, 6, [[0, None, 2], [3, 4, 5]]),
                 zone('Underglow Left', 6, 2), zone('Underglow Right', 8, 2),
                 zone('Underglow Wrist Rest', 10, 20), zone('Void', 30, 2)]
        device = SimpleNamespace(name='Razer Blackwidow V4 Pro', leds=[None] * 32,
                                 data=SimpleNamespace(zones=zones))
        steps, positions = rgb.accessory_layout(device)
        self.assertEqual(steps, 5)
        self.assertEqual(positions[:10], [1, None, 3, 1, 2, 3, 0, 0, 4, 4])
        self.assertEqual(positions[10:14], [0] * 4)
        self.assertEqual(positions[26:30], [4] * 4)
        self.assertEqual(positions[30:], [None, None])
        axis = rgb.ripple_colors(steps, .4, (100, 255, 60), .2, keep_lit=True)
        self.assertEqual(axis[positions[0]], (100, 255, 60))
        self.assertEqual(axis[positions[3]], (100, 255, 60))
        self.assertEqual(axis[positions[2]], (40, 102, 24))

    def test_mouse_layout_fills_left_to_right_in_physical_columns(self):
        from types import SimpleNamespace
        device = SimpleNamespace(name='Razer Basilisk V3 Pro 35K Phantom Green (Wireless)',
            leds=[None] * 12, data=SimpleNamespace(zones=[
                SimpleNamespace(name='Scroll Wheel', start_idx=0, num_leds=1),
                SimpleNamespace(name='LED Strip', start_idx=1, num_leds=11)]))
        steps, positions = rgb.accessory_layout(device)
        self.assertEqual((steps, positions), (6, [3, 0, 0, 0, 0, 1, 3, 4, 5, 5, 5, 5]))
        color = (100, 255, 60)
        axis = rgb.ripple_colors(steps, .5, color, .3, reveal=True)
        frame = [axis[p] for p in positions]
        self.assertEqual(frame[5], color)
        self.assertEqual(frame[1:5], [(75, 191, 45)] * 4)
        self.assertEqual(frame[0], (0, 0, 0))
        self.assertEqual(frame[7:], [(0, 0, 0)] * 5)
        settled = rgb.ripple_colors(steps, .5, color, 1.5, reveal=True)
        self.assertEqual([settled[p] for p in positions],
                         [(0, 0, 0)] + [(50, 128, 30)] * 5 + [(0, 0, 0)] * 6)

    def test_accessory_sends_one_complete_frame_and_confirms_it(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        device = SimpleNamespace(id=7, zones=[SimpleNamespace(set_colors=Mock())],
                                 update=Mock(), colors=[])
        def set_colors(colors, fast=False):
            self.assertTrue(fast)
            device.colors = colors
        device.set_colors = Mock(side_effect=set_colors)
        hardware = rgb.Hardware.__new__(rgb.Hardware)
        hardware.devices = {'accessories': [device]}
        hardware.accessory_layouts = {7: (3, [0, 1, 2, None])}
        hardware.previous_progress = {}
        hardware.rgb_color = lambda *channels: channels
        hardware.theme = SimpleNamespace(accent=lambda *args: (100, 255, 60))
        hardware.write_progress(3.1, 0, brightness=.1)
        hardware.write_progress(3.1, 0, brightness=.1)
        self.assertEqual(device.colors, [(0, 0, 0)] * 4)
        device.set_colors.assert_called_once()
        device.update.assert_called_once()
        hardware.write_progress(3.1, .25, brightness=.1)
        self.assertEqual(device.colors, [(100, 255, 60), (0, 0, 0), (0, 0, 0), (0, 0, 0)])
        hardware.write_progress(3.1, 3, brightness=.1)
        self.assertEqual(device.colors, [(0, 0, 0)] * 4)
        device.zones[0].set_colors.assert_not_called()

    def test_gpu_and_accessory_share_a_cycle_when_progress_changes(self):
        from types import SimpleNamespace
        color = (100, 255, 60)
        zone = SimpleNamespace(name='GPU', leds=[None] * 30, colors=[])
        def set_zone(colors, fast=False):
            zone.colors = colors
        zone.set_colors = set_zone
        accessory = SimpleNamespace(id=1, zones=[], colors=[])
        def set_accessory(colors, fast=False):
            accessory.colors = colors
        accessory.set_colors = set_accessory
        accessory.update = lambda: None
        ram = linear_device(2, [('RAM', 30)])
        chassis = linear_device(3, [('Mainboard', 30)])
        hardware = rgb.Hardware.__new__(rgb.Hardware)
        hardware.devices = {'gpu': [SimpleNamespace(id=0, zones=[zone], update=lambda: None)],
                            'accessories': [accessory], 'ram': [ram], 'chassis': [chassis]}
        hardware.accessory_layouts = {1: (30, list(range(30)))}
        hardware.previous_progress = {}
        hardware.rgb_color = lambda *channels: channels
        hardware.theme = SimpleNamespace(accent=lambda *args: color)
        hardware.write_progress(3.49, 216700, brightness=.49)
        hardware.write_progress(3.49, 216700.6, brightness=.49)
        self.assertEqual(zone.colors.index(color), 2)
        self.assertEqual(accessory.colors.index(color), 2)
        self.assertEqual(ram.colors.index(color), 10)
        self.assertEqual(chassis.colors.index(color), 10)
        hardware.write_progress(3.49001, 216700.65, brightness=.49001)
        self.assertEqual(zone.colors.index(color), 3)
        self.assertEqual(accessory.colors.index(color), 3)
        self.assertEqual(ram.colors.index(color), 11)
        self.assertEqual(chassis.colors.index(color), 11)

    def test_ram_sticks_ripple_in_parallel_with_unfilled_background_lit(self):
        device = linear_device(0, [('Fury Slot 1', 12), ('Fury Slot 2', 12)])
        color = (100, 255, 60)
        frame = rgb.linear_device_ripple_colors(device, .5, color, 1 / 3)
        self.assertEqual(frame[:12], frame[12:])
        self.assertEqual(frame[3], color)
        self.assertEqual(frame[6:12], [(50, 128, 30)] * 6)
        self.assertEqual(rgb.linear_device_ripple_colors(device, 0, color, 0), [(0, 0, 0)] * 24)

    def test_chassis_keeps_each_header_independent_and_skips_void(self):
        device = linear_device(0, [('Aura Mainboard', 3), ('Header 1', 120),
                                  ('Header 2', 120), ('Header 3', 120), ('Void', 2)])
        color = (100, 255, 60)
        frame = rgb.linear_device_ripple_colors(device, .5, color, 1 / 3)
        self.assertEqual(len(frame), 365)
        self.assertEqual(frame[1], color)
        self.assertEqual(frame[3 + 21], color)
        self.assertEqual(frame[3:123], frame[123:243])
        self.assertEqual(frame[123:243], frame[243:363])
        self.assertEqual(frame[3 + 60:123], [(50, 128, 30)] * 60)
        self.assertEqual(frame[-2:], [(0, 0, 0)] * 2)

    def test_ram_and_chassis_use_one_confirmed_frame_per_controller(self):
        from types import SimpleNamespace
        ram = linear_device(0, [('Slot 1', 12), ('Slot 2', 12)])
        chassis = linear_device(1, [('Mainboard', 3), ('Header', 120)])
        hardware = rgb.Hardware.__new__(rgb.Hardware)
        hardware.devices = {'ram': [ram], 'chassis': [chassis]}
        hardware.previous_progress = {}
        hardware.rgb_color = lambda *channels: channels
        hardware.theme = SimpleNamespace(accent=lambda *args: (100, 255, 60))
        hardware.write_progress(3.5, 1 / 3, brightness=.5)
        hardware.write_progress(3.5, 1 / 3, brightness=.5)
        for device in [ram, chassis]:
            device.set_colors.assert_called_once()
            device.update.assert_called_once()
            for zone in device.zones:
                zone.set_colors.assert_not_called()
            self.assertEqual(hardware.previous_progress[(device.id, None)], device.colors)

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
        from unittest.mock import Mock
        device = linear_device(0, [('RAM', 1)])
        device.set_colors = Mock()
        hardware = rgb.Hardware.__new__(rgb.Hardware)
        hardware.devices = {'ram': [device]}
        hardware.previous_progress = {}
        hardware.ripple_cycle = rgb.GPURippleCycle()
        hardware.ripple_cycle.phase(.5, 0)
        hardware.theme = SimpleNamespace(accent=lambda *args: (100, 255, 60))
        hardware.rgb_color = lambda *channels: channels
        with patch.object(rgb.time, 'monotonic', side_effect=[0, 11]):
            with self.assertRaisesRegex(RuntimeError, 'RGB frame was not confirmed'):
                hardware.write_progress(2.5, 1.5, brightness=.5)
        self.assertEqual(hardware.previous_progress, {})

    def test_slow_controller_is_polled_without_resending_the_frame(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        device = linear_device(0, [('RAM', 1)])
        device.set_colors = Mock()
        updates = []
        def update():
            updates.append(True)
            if len(updates) == 2:
                device.colors = [(50, 128, 30)]
        device.update = update
        hardware = rgb.Hardware.__new__(rgb.Hardware)
        hardware.devices = {'ram': [device]}
        hardware.previous_progress = {}
        hardware.ripple_cycle = rgb.GPURippleCycle()
        hardware.ripple_cycle.phase(.5, 0)
        hardware.theme = SimpleNamespace(accent=lambda *args: (100, 255, 60))
        hardware.rgb_color = lambda *channels: channels
        with patch.object(rgb.time, 'sleep'):
            hardware.write_progress(2.5, 1.5, brightness=.5)
        self.assertEqual(len(updates), 2)
        device.set_colors.assert_called_once()
        self.assertEqual(hardware.previous_progress, {(0, None): [(50, 128, 30)]})
