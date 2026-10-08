import base64
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import threading

from PIL import Image, ImageDraw
from easy_action.model import Store, new_macro, validate, normalize_hotkey
from easy_action.desktop import image_matches, save_screenshot
from easy_action.accessibility import matching


def sample():
    macro = new_macro()
    macro.update(name='テスト', hotkey='ctrl+alt+f10', steps=[{'kind': 'wait', 'seconds': .1}])
    return macro


class ModelTests(unittest.TestCase):
    def test_limit_and_duplicate_hotkey(self):
        macros = []
        for index in range(100):
            macro = sample()
            # Unique combinations; physical use needn't consume all of them.
            macro['hotkey'] = ('ctrl+alt+' if index < 50 else 'ctrl+shift+') + chr(0x4e00 + index % 50)
            macros.append(macro)
        validate(macros)
        with self.assertRaises(ValueError):
            validate(macros + [sample()])
        other = sample()
        other['hotkey'] = 'alt+ctrl+f10'
        with self.assertRaises(ValueError):
            validate([sample(), other])

    def test_reserved_keys_and_invalid_numbers(self):
        for key in ('esc', 'ctrl+esc', 'f8', 'a', 'ctrl+alt', 'ctrl+ctrl+a'):
            with self.assertRaises(ValueError, msg=key):
                normalize_hotkey(key)
        for bad in (float('nan'), float('inf'), -1, True):
            macro = sample()
            macro['steps'][0]['seconds'] = bad
            with self.assertRaises(ValueError):
                validate([macro])

    def test_backup_and_invalid_save_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'actions.json')
            macro = sample()
            store.save([macro])
            old = store.path.read_bytes()
            macro['name'] = '変更後'
            store.save([macro])
            self.assertEqual(store.load()[0]['name'], '変更後')
            self.assertEqual(store.path.with_suffix('.backup.json').read_bytes(), old)
            stored = store.path.read_bytes()
            macro['steps'] = []
            with self.assertRaises(ValueError):
                store.save([macro])
            self.assertEqual(store.path.read_bytes(), stored)

    def test_unreleased_key_rejected(self):
        macro = sample()
        macro['steps'] = [{'kind': 'key_down', 'keys': 'ctrl'}]
        with self.assertRaises(ValueError):
            validate([macro])
        macro['steps'].append({'kind': 'key_up', 'keys': 'ctrl'})
        validate([macro])

    def test_corrupt_file_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'actions.json')
            store.path.write_text('{"version": 2, "macros": []}')
            with self.assertRaises(ValueError):
                store.load()


class ImageTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        rng = np.random.default_rng(42)
        self.template = Image.fromarray(rng.integers(0, 255, (24, 32, 3), dtype=np.uint8))
        stream = io.BytesIO()
        self.template.save(stream, 'PNG')
        self.encoded = base64.b64encode(stream.getvalue()).decode()

    def test_match_uses_monitor_origin_and_scale(self):
        screen = Image.new('RGB', (600, 400), 'white')
        screen.paste(self.template, (80, 60))
        found = image_matches([(screen, (-300, 0, 300, 200))], self.encoded, .95)
        self.assertEqual(found, (-252, 36))

    def test_ambiguous_match_stops(self):
        screen = Image.new('RGB', (600, 400), 'white')
        screen.paste(self.template, (80, 60))
        screen.paste(self.template, (350, 220))
        with self.assertRaises(RuntimeError):
            image_matches([(screen, (0, 0, 600, 400))], self.encoded, .95)

    def test_missing_match_returns_none_and_bad_image_rejected(self):
        screen = Image.new('RGB', (600, 400), 'white')
        self.assertIsNone(image_matches([(screen, (0, 0, 600, 400))], self.encoded, .95))
        with self.assertRaises(ValueError):
            image_matches([(screen, (0, 0, 600, 400))], 'broken!', .95)

    def test_screenshot_one_file_per_display(self):
        with tempfile.TemporaryDirectory() as folder:
            screens = [(self.template, (0, 0, 32, 24)), (self.template, (-32, 0, 32, 24))]
            files = save_screenshot(screens, folder)
            self.assertEqual(len(files), 2)
            self.assertTrue(all(Path(path).exists() for path in files))


class AccessibilityTests(unittest.TestCase):
    def test_target_identity_is_scoped_to_app_role_and_os(self):
        target = {'platform': 'win32', 'app': 'chrome.exe', 'role': 'Button', 'name': '次へ', 'identifier': 'next'}
        changed_label = dict(target, name='Next')
        self.assertTrue(matching(changed_label, target))
        self.assertFalse(matching(dict(target, app='other.exe'), target))
        self.assertFalse(matching(dict(target, role='Text'), target))
        self.assertFalse(matching(dict(target, platform='darwin'), target))
        self.assertFalse(matching(dict(target, identifier='previous'), target))


if __name__ == '__main__':
    unittest.main()
