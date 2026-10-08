"""Persistence and validation, independent of the desktop libraries."""
import copy
import json
import math
import os
from pathlib import Path
import sys
import uuid

MAX_MACROS = 100
KINDS = {
    'move': 'マウスを移動', 'click': '指定した場所をクリック',
    'drag': 'つかんで移動', 'scroll': '画面をスクロール',
    'text': '文字を入力', 'keys': 'キー・ショートカットを押す',
    'wait': '少し待つ', 'image': '画像を探してクリック',
    'element': 'ボタンを認識してクリック', 'screenshot': 'スクリーンショットを保存',
    'check': 'チェックをオン・オフにする',
    'key_down': 'キーを押したままにする', 'key_up': 'キーを離す',
}
MODIFIERS = {'ctrl', 'alt', 'shift', 'cmd'}
NAMED_KEYS = {'enter', 'tab', 'space', 'backspace', 'delete', 'esc', 'up', 'down',
              'left', 'right', 'home', 'end', 'page_up', 'page_down', 'insert',
              'caps_lock', 'print_screen', 'plus', *MODIFIERS, *{f'f{i}' for i in range(1, 25)}}


def key_tokens(value):
    parts = [p.strip().lower() for p in value.split('+') if p.strip()]
    if not parts or any(len(p) != 1 and p not in NAMED_KEYS for p in parts):
        raise ValueError('使えないキーが含まれています。入力欄で実際のキーを押してください。')
    if len(parts) != len(set(parts)):
        raise ValueError('同じキーが重複しています。')
    return parts


def normalize_hotkey(value):
    parts = key_tokens(value)
    nonmod = [p for p in parts if p not in MODIFIERS]
    if len(nonmod) != 1:
        raise ValueError('呼び出すキーは、文字またはFキーを1つ指定してください。')
    if nonmod[0] in ('esc', 'f7', 'f8'):
        raise ValueError('Esc・F7・F8は停止・記録用です。別のキーを選んでください。')
    if not (set(parts) & MODIFIERS) and not nonmod[0].startswith('f'):
        raise ValueError('Ctrl / Alt / Shift / Command と組み合わせてください。')
    return '+'.join([p for p in ('ctrl', 'alt', 'shift', 'cmd') if p in parts] + nonmod)


def new_macro():
    return {'id': uuid.uuid4().hex, 'name': '新しい操作', 'hotkey': '',
            'enabled': True, 'steps': []}


def number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label}の値を確認してください。')


def validate_step(step):
    if not isinstance(step, dict) or step.get('kind') not in KINDS:
        raise ValueError('対応していない動作です。')
    kind = step['kind']
    if kind in ('move', 'click', 'drag'):
        for field in ('x', 'y'):
            number(step.get(field), '場所', -100000, 100000)
    if kind == 'drag':
        for field in ('to_x', 'to_y'):
            number(step.get(field), '移動先', -100000, 100000)
    if kind in ('move', 'drag'):
        number(step.get('duration', .3), '移動時間', .05, 60)
    if kind in ('click', 'image', 'element'):
        if step.get('button', 'left') not in ('left', 'right', 'middle'):
            raise ValueError('クリックの種類を確認してください。')
        if step.get('count', 1) not in (1, 2):
            raise ValueError('クリック回数を確認してください。')
    if kind in ('wait', 'image', 'element', 'check'):
        number(step.get('seconds', 10 if kind != 'wait' else 1), '待つ時間', 0, 3600)
    if kind == 'image':
        if not isinstance(step.get('png'), str) or len(step['png']) > 8_000_000:
            raise ValueError('画像を指定してください（6MBまで）。')
        number(step.get('threshold', .92), '画像の一致度', .8, 1)
    if kind in ('element', 'check') and not isinstance(step.get('target'), dict):
        raise ValueError('ボタンを選び直してください。')
    if kind == 'check' and not isinstance(step.get('checked'), bool):
        raise ValueError('チェックをオン・オフのどちらにするか選んでください。')
    if kind == 'text' and (not isinstance(step.get('text'), str) or len(step['text']) > 100000):
        raise ValueError('入力する文章を確認してください。')
    if kind in ('keys', 'key_down', 'key_up'):
        parts = key_tokens(step.get('keys', ''))
        if kind != 'keys' and len(parts) != 1:
            raise ValueError('押したままにするキーは1つ指定してください。')
        if 'esc' in parts:
            raise ValueError('Escは停止用なので記録・送信できません。')
    if kind == 'scroll':
        number(step.get('amount'), 'スクロール量', -1000, 1000)
    if kind == 'screenshot' and (not isinstance(step.get('folder'), str) or not step['folder'].strip()):
        raise ValueError('画像を保存するフォルダを指定してください。')


def validate(macros):
    if not isinstance(macros, list) or len(macros) > MAX_MACROS:
        raise ValueError('登録できる操作は100件までです。')
    seen_ids, seen_keys = set(), set()
    for macro in macros:
        if not isinstance(macro, dict):
            raise ValueError('操作のデータ形式を確認してください。')
        mid = macro.get('id')
        if not isinstance(mid, str) or not mid or mid in seen_ids:
            raise ValueError('操作の識別番号が重複しています。')
        seen_ids.add(mid)
        if not isinstance(macro.get('name'), str) or not macro['name'].strip() or len(macro['name']) > 80:
            raise ValueError('操作の名前は1〜80文字で指定してください。')
        if not isinstance(macro.get('enabled'), bool):
            raise ValueError('有効・無効の設定を確認してください。')
        hotkey = normalize_hotkey(macro.get('hotkey', ''))
        if hotkey in seen_keys:
            raise ValueError('同じ呼び出しキーが登録済みです。別のキーを選んでください。')
        seen_keys.add(hotkey)
        if not isinstance(macro.get('steps'), list) or not 1 <= len(macro['steps']) <= 2000:
            raise ValueError('動作は1〜2000手順で登録してください。')
        for step in macro['steps']:
            validate_step(step)
        held = set()
        for step in macro['steps']:
            if step['kind'] == 'key_down':
                held.add(step['keys'])
            elif step['kind'] == 'key_up':
                held.discard(step['keys'])
        if held:
            raise ValueError('押したままのキーがあります。「キーを離す」を追加してください。')


def data_folder():
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home())))
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    return base / 'EasyActionHotkeys'


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else data_folder() / 'actions.json'

    def load(self):
        if not self.path.exists():
            return []
        content = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(content, dict) or content.get('version') != 1:
            raise ValueError('この保存データのバージョンには対応していません。')
        macros = content.get('macros')
        validate(macros)
        return macros

    def save(self, macros):
        validate(macros)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({'version': 1, 'macros': macros}, ensure_ascii=False, indent=2)
        temp = self.path.with_suffix('.tmp')
        with temp.open('w', encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if self.path.exists():
            self.path.with_suffix('.backup.json').write_bytes(self.path.read_bytes())
        temp.replace(self.path)


def describe(step):
    kind = step['kind']
    if kind == 'text':
        return '文字を入力：' + step['text'].replace('\n', ' ↵ ')[:55]
    if kind in ('keys', 'key_down', 'key_up'):
        return KINDS[kind] + '：' + step['keys'].upper()
    if kind == 'wait':
        return f"{step['seconds']:g}秒待つ"
    if kind in ('move', 'click'):
        return f"{KINDS[kind]}：({step['x']}, {step['y']})"
    if kind == 'drag':
        return f"つかんで移動：({step['x']}, {step['y']}) → ({step['to_x']}, {step['to_y']})"
    if kind == 'scroll':
        return f"{'上' if step['amount'] > 0 else '下'}にスクロール：{abs(step['amount'])}"
    if kind in ('element', 'check'):
        if kind == 'check':
            return f"チェックを{'オン' if step['checked'] else 'オフ'}：" + step['target'].get('name', '選択した項目')
        return 'ボタンをクリック：' + step['target'].get('name', '選択したボタン')
    if kind == 'image':
        return f"画像を探してクリック（最大{step.get('seconds', 10):g}秒待つ）"
    return KINDS[kind]
