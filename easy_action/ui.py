import base64
import copy
import json
import os
from pathlib import Path
import sys
import uuid

from PySide6.QtCore import Qt, QTimer, QRect, QBuffer, QIODevice, QStandardPaths, QLockFile
from PySide6.QtGui import QCursor, QPainter, QColor, QPen, QPixmap, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QPushButton, QListWidget, QLineEdit, QTextEdit,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QMessageBox, QFileDialog,
    QDialogButtonBox, QSplitter, QFrame, QProgressBar, QScrollArea,
)
from .model import (Store, MAX_MACROS, KINDS, new_macro, describe, validate,
                    validate_step, normalize_hotkey, data_folder)
from .desktop import ScreenService
from .input import InputHub
from .runner import Runner
from .accessibility import capture_target, checkbox_snapshot

STYLE = '''
QWidget { font-family: "Yu Gothic UI", "Hiragino Sans", sans-serif; font-size: 14px; color: #24324a; }
QMainWindow, QDialog { background: #f3f6fb; }
QLabel#title { font-size: 26px; font-weight: bold; color: #193660; }
QLabel#muted { color: #61728b; }
QFrame#card { background: white; border: 1px solid #dde5ef; border-radius: 12px; }
QPushButton { background: white; border: 1px solid #bdcde0; border-radius: 7px; padding: 9px 15px; }
QPushButton:hover { background: #edf4ff; }
QPushButton:disabled { color: #a2adba; background: #edf0f4; }
QPushButton#primary { background: #2563eb; border-color: #2563eb; color: white; font-weight: bold; }
QPushButton#danger { color: #a82a35; border-color: #e5b7bc; }
QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: white; border: 1px solid #bdcde0; border-radius: 6px; padding: 7px; }
QListWidget { background: white; border: 1px solid #dde5ef; border-radius: 8px; padding: 5px; }
QListWidget::item { padding: 12px 8px; border-bottom: 1px solid #eef2f7; }
QListWidget::item:selected { color: #1847a0; background: #e7efff; border-radius: 5px; }
QProgressBar { border: 1px solid #d6e0ee; border-radius: 5px; height: 10px; }
QProgressBar::chunk { background: #2563eb; }
'''


def button(text, callback, primary=False, danger=False):
    obj = QPushButton(text)
    obj.clicked.connect(callback)
    if primary:
        obj.setObjectName('primary')
    if danger:
        obj.setObjectName('danger')
    return obj


def label(text, muted=False):
    obj = QLabel(text)
    obj.setWordWrap(True)
    if muted:
        obj.setObjectName('muted')
    return obj


def pictures_folder():
    path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    return str(Path(path or Path.home()) / 'EasyActionHotkeys')


class KeyBox(QLineEdit):
    def __init__(self, parent=None, single_key=False):
        super().__init__(parent)
        self.single_key = single_key
        self.setPlaceholderText('ここをクリックして、登録するキーを押してください')
        self.setReadOnly(True)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            if self.single_key:
                self.setText({Qt.Key.Key_Control: 'ctrl', Qt.Key.Key_Shift: 'shift',
                              Qt.Key.Key_Alt: 'alt', Qt.Key.Key_Meta: 'cmd'}[event.key()])
            return
        key = QKeySequence(event.key()).toString(QKeySequence.SequenceFormat.PortableText).lower()
        key = {'return': 'enter', 'del': 'delete', 'pgup': 'page_up', 'pgdown': 'page_down',
               'space': 'space', 'ins': 'insert'}.get(key, key)
        if key == '+':
            key = 'plus'
        if not key:
            return
        modifiers = []
        for flag, name in ((Qt.KeyboardModifier.ControlModifier, 'ctrl'),
                           (Qt.KeyboardModifier.AltModifier, 'alt'),
                           (Qt.KeyboardModifier.ShiftModifier, 'shift'),
                           (Qt.KeyboardModifier.MetaModifier, 'cmd')):
            if event.modifiers() & flag:
                modifiers.append(name)
        self.setText(key if self.single_key else '+'.join(modifiers + [key]))
        event.accept()


class RegionPicker(QDialog):
    """Freeze one monitor and let the user draw a rectangle in logical pixels."""
    def __init__(self, screen):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setGeometry(screen.geometry())
        self.pixmap = screen.grabWindow(0)
        self.start_point = None
        self.rect = QRect()
        self.png = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect_for_screen(), self.pixmap)
        painter.fillRect(self.rect_for_screen(), QColor(15, 25, 45, 100))
        if not self.rect.isNull():
            painter.setPen(QPen(QColor('#6aa9ff'), 3))
            painter.drawRect(self.rect)
        painter.setPen(Qt.GlobalColor.white)
        painter.drawText(30, 42, '対象の画像をドラッグで囲んでください。Escで戻ります。')

    def rect_for_screen(self):
        return QRect(0, 0, self.width(), self.height())

    def mousePressEvent(self, event):
        self.start_point = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self.start_point:
            self.rect = QRect(self.start_point, event.position().toPoint()).normalized().intersected(self.rect_for_screen())
            self.update()

    def mouseReleaseEvent(self, event):
        if self.rect.width() < 8 or self.rect.height() < 8:
            return
        sx = self.pixmap.width() / self.width()
        sy = self.pixmap.height() / self.height()
        crop = self.pixmap.copy(int(self.rect.x()*sx), int(self.rect.y()*sy),
                                int(self.rect.width()*sx), int(self.rect.height()*sy))
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        crop.save(buffer, 'PNG')
        self.png = base64.b64encode(bytes(buffer.data())).decode('ascii')
        self.accept()


class StepDialog(QDialog):
    def __init__(self, owner, step=None):
        super().__init__(owner)
        self.owner = owner
        self.step = copy.deepcopy(step or {'kind': 'click', 'x': 0, 'y': 0})
        self.result_step = None
        self.setWindowTitle('動きを追加・変更')
        self.resize(580, 590)
        self.layout = QVBoxLayout(self)
        self.layout.addWidget(label('どんな動きをさせますか？'))
        self.kind = QComboBox()
        for key, title in KINDS.items():
            self.kind.addItem(title, key)
        self.kind.setCurrentIndex(list(KINDS).index(self.step['kind']))
        self.layout.addWidget(self.kind)
        self.help = label('', True)
        self.layout.addWidget(self.help)
        self.fields = QWidget()
        self.form = QFormLayout(self.fields)
        self.layout.addWidget(self.fields)
        self.layout.addStretch()
        self.status = label('', True)
        self.layout.addWidget(self.status)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        controls.button(QDialogButtonBox.StandardButton.Ok).setText('この動きを使う')
        controls.button(QDialogButtonBox.StandardButton.Cancel).setText('戻る')
        controls.accepted.connect(self.save)
        controls.rejected.connect(self.reject)
        self.layout.addWidget(controls)
        self.kind.currentIndexChanged.connect(self.build_fields)
        self.build_fields()

    def spin(self, name, default, minimum, maximum, decimal=False):
        box = QDoubleSpinBox() if decimal else QSpinBox()
        box.setRange(minimum, maximum)
        box.setValue(self.step.get(name, default))
        if decimal:
            box.setDecimals(2)
            box.setSingleStep(.1)
        return box

    def build_fields(self):
        while self.form.count():
            item = self.form.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        kind = self.kind.currentData()
        self.widgets = {}
        self.help.setText({
            'click': '「画面で場所を選ぶ」を押し、4秒以内にクリックしたい場所へマウスを置きます。クリックは不要です。',
            'move': '画面上で移動先を選べます。座標を入力する必要はありません。',
            'drag': 'つかむ場所と移動先をそれぞれ選びます。',
            'text': '日本語や改行も入力できます。入力先をクリックする動きを、先に追加してください。',
            'keys': 'Ctrl+C、Command+Sなど、実際のキーを押して設定します。Escは停止用です。',
            'image': '画面上のボタンや文字を囲んで登録します。見つかるまで待ち、複数見つかる場合は停止します。',
            'element': '対象のアプリを手前に出してボタンを指します。位置が変わっても、ボタンの情報から探します。',
            'check': 'チェック項目を指して登録します。実行時の状態を確認し、指定したオン・オフに合わせます。',
            'screenshot': '接続している画面をそれぞれPNGで保存します。保存するフォルダを選べます。',
            'wait': '次の操作まで待つ時間です。ページの読み込み待ちには画像・ボタン指定も使えます。',
            'key_down': '操作の記録で使う動作です。最後に「キーを離す」を入れてください。',
            'key_up': '押したままにしているキーを離します。',
            'scroll': 'プラスで上、マイナスで下にスクロールします。',
        }.get(kind, ''))
        if kind in ('click', 'move', 'drag'):
            for prefix, title in (('', '場所'), ('to_', '移動先')):
                if prefix and kind != 'drag':
                    continue
                row = QWidget()
                horizontal = QHBoxLayout(row)
                horizontal.setContentsMargins(0, 0, 0, 0)
                for axis in ('x', 'y'):
                    name = prefix + axis
                    widget = self.spin(name, 0, -100000, 100000)
                    self.widgets[name] = widget
                    horizontal.addWidget(widget)
                horizontal.addWidget(button('画面で選ぶ', lambda checked=False, p=prefix: self.pick_point(p)))
                self.form.addRow(title, row)
        if kind in ('move', 'drag'):
            self.widgets['duration'] = self.spin('duration', .3, .05, 60, True)
            self.form.addRow('移動にかける秒数', self.widgets['duration'])
        if kind in ('click', 'image', 'element'):
            self.widgets['button'] = QComboBox()
            for title, value in (('左クリック', 'left'), ('右クリック', 'right'), ('中央クリック', 'middle')):
                self.widgets['button'].addItem(title, value)
            self.widgets['button'].setCurrentIndex(self.widgets['button'].findData(self.step.get('button', 'left')))
            self.form.addRow('クリックの種類', self.widgets['button'])
            self.widgets['count'] = self.spin('count', 1, 1, 2)
            self.form.addRow('回数（2でダブルクリック）', self.widgets['count'])
        if kind in ('wait', 'image', 'element', 'check'):
            self.widgets['seconds'] = self.spin('seconds', 1 if kind == 'wait' else 10, 0, 3600, True)
            self.form.addRow('待つ秒数（最大）', self.widgets['seconds'])
        if kind == 'text':
            self.widgets['text'] = QTextEdit()
            self.widgets['text'].setPlainText(self.step.get('text', ''))
            self.widgets['text'].setPlaceholderText('入力したい文章を書いてください')
            self.form.addRow('入力する文章', self.widgets['text'])
        if kind in ('keys', 'key_down', 'key_up'):
            self.widgets['keys'] = KeyBox(single_key=kind != 'keys')
            self.widgets['keys'].setText(self.step.get('keys', ''))
            self.form.addRow('送信するキー', self.widgets['keys'])
        if kind == 'scroll':
            self.widgets['amount'] = self.spin('amount', -3, -1000, 1000)
            self.form.addRow('スクロール量', self.widgets['amount'])
        if kind == 'screenshot':
            self.widgets['folder'] = QLineEdit(self.step.get('folder', pictures_folder()))
            self.form.addRow('保存先', self.widgets['folder'])
            self.form.addRow(button('フォルダを選ぶ', self.pick_folder))
        if kind == 'image':
            self.screen = QComboBox()
            for index, screen in enumerate(QApplication.screens()):
                self.screen.addItem(f'画面 {index + 1}：{screen.name()}', index)
            self.form.addRow('対象の画面', self.screen)
            self.form.addRow(button('画面で画像を囲む', self.pick_image, True))
            self.preview = QLabel()
            self.preview.setMinimumHeight(70)
            self.form.addRow(self.preview)
            self.show_preview()
            self.widgets['threshold'] = self.spin('threshold', .92, .8, 1, True)
            self.form.addRow('画像の一致度', self.widgets['threshold'])
        if kind in ('element', 'check'):
            self.form.addRow(button('画面でボタンを選ぶ（4秒後）', self.pick_element, True))
            self.target_label = label(self.step.get('target', {}).get('name', 'まだ選ばれていません'), True)
            self.form.addRow(self.target_label)
            if kind == 'check':
                self.widgets['checked'] = QCheckBox('オンにする（外すとオフにする）')
                self.widgets['checked'].setChecked(self.step.get('checked', True))
                self.form.addRow(self.widgets['checked'])

    def pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, '画像を保存するフォルダ')
        if path:
            self.widgets['folder'].setText(path)

    def _hide_for_capture(self, callback, delay=4000):
        self.owner.hub.suspended = True
        self.hide()
        self.owner.hide()
        self.owner.capture_notice.setText('4秒以内に対象へマウスを置いてください。クリックは不要です。' if delay == 4000 else '画像を選ぶ画面を準備しています。')
        if delay == 4000:
            self.owner.capture_notice.show()
        def finish():
            self.owner.capture_notice.hide()
            try:
                callback()
            except Exception as exc:
                QMessageBox.warning(self, '対象を選べませんでした', str(exc))
            finally:
                self.owner.show()
                self.show()
                self.activateWindow()
        QTimer.singleShot(delay, finish)

    def pick_point(self, prefix):
        def capture():
            point = QCursor.pos()
            self.widgets[prefix+'x'].setValue(point.x())
            self.widgets[prefix+'y'].setValue(point.y())
        self._hide_for_capture(capture)

    def pick_element(self):
        def capture():
            point = QCursor.pos()
            self.step['target'] = capture_target(point.x(), point.y())
            self.target_label.setText(self.step['target']['name'] or self.step['target']['identifier'])
        self._hide_for_capture(capture)

    def pick_image(self):
        index = self.screen.currentData()
        def capture():
            picker = RegionPicker(QApplication.screens()[index])
            if picker.exec() == QDialog.DialogCode.Accepted and picker.png:
                self.step['png'] = picker.png
                self.show_preview()
        self._hide_for_capture(capture, 500)

    def show_preview(self):
        if self.step.get('png'):
            pixmap = QPixmap()
            pixmap.loadFromData(base64.b64decode(self.step['png']))
            self.preview.setPixmap(pixmap.scaled(280, 100, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.preview.setText('まだ画像が選ばれていません')

    def save(self):
        kind = self.kind.currentData()
        result = {'kind': kind}
        for name, widget in self.widgets.items():
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QComboBox):
                value = widget.currentData()
            elif isinstance(widget, QTextEdit):
                value = widget.toPlainText()
            elif isinstance(widget, QLineEdit):
                value = widget.text()
            else:
                value = widget.value()
            result[name] = value
        if kind in ('image', 'element', 'check'):
            field = 'png' if kind == 'image' else 'target'
            if field not in self.step:
                self.status.setText('先に対象を選んでください。')
                return
            result[field] = self.step[field]
        try:
            validate_step(result)
        except (ValueError, TypeError) as exc:
            self.status.setText(str(exc))
            return
        self.result_step = result
        self.accept()


class HistoryGuide(QDialog):
    """Guide the user through recording the actual browser controls. No guessing
of destructive browser settings or deprecated password-removal APIs."""
    def __init__(self, owner):
        super().__init__(owner)
        self.setWindowTitle('Chromeの履歴削除を登録')
        self.resize(570, 540)
        layout = QVBoxLayout(self)
        layout.addWidget(label('削除したい期間と項目を決めてから、実際の操作を記録します。'))
        form = QFormLayout()
        self.period = QComboBox()
        self.period.addItems(['過去15分間（Chromeに表示される場合）', '過去1時間', '過去24時間', '過去7日間', '過去4週間', '全期間'])
        self.period.setCurrentIndex(1)
        form.addRow('削除する期間', self.period)
        layout.addLayout(form)
        self.items = []
        for name in ['閲覧履歴', 'Cookieとサイトデータ', 'キャッシュ', 'ダウンロード履歴', '自動入力フォームのデータ', 'サイトの設定']:
            box = QCheckBox(name)
            box.setChecked(name == '閲覧履歴')
            self.items.append(box)
            layout.addWidget(box)
        layout.addWidget(label('1. Chromeを手前に出し、履歴削除の画面を開きます。\n2. 期間を変更し、削除する項目を確認します。\n3. F7でチェック項目のオン・オフを記録します。\n4. 削除ボタンを押し、F8で記録を止めます。\n5. 記録された動きを確認し、必要ならクリックをボタン認識に置き換えます。', True))
        layout.addWidget(label('この画面の選択は記録時の案内です。実際のChrome画面でも同じ期間・項目を選んでください。登録中の削除操作も実際に実行されます。', True))
        layout.addWidget(label('保存パスワード・パスキーは、Chromeのパスワードマネージャーで項目を指定する操作を、別の登録として記録してください。', True))
        layout.addWidget(button('この内容で操作を記録する', self.accept, True))
        layout.addWidget(button('戻る', self.reject))

    def instructions(self):
        selected = '、'.join(box.text() for box in self.items if box.isChecked()) or 'なし'
        return f'期間：{self.period.currentText()} ／ 削除：{selected}。Chrome画面でも同じ設定にしてください。'


class MainWindow(QMainWindow):
    def __init__(self, store=None, start_listeners=True):
        super().__init__()
        self.setWindowTitle('かんたん操作ショートカット')
        available = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1180, max(500, available.width()-40)), min(820, max(400, available.height()-60)))
        self.store = store or Store()
        self.macros = []
        self.current_id = None
        self.draft = None
        self.dirty = False
        self.loading = False
        self.recording_steps = []
        self.record_pending = False
        self.armed_before = False
        self.hub = InputHub()
        self.screens = ScreenService()
        self.runner = Runner(self.screens, self.hub)
        self.capture_notice = QLabel(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.capture_notice.setWordWrap(True)
        self.capture_notice.setStyleSheet('background: #193660; color: white; padding: 16px; font-size: 14px;')
        self.capture_notice.resize(430, 100)
        self.capture_notice.move(QApplication.primaryScreen().availableGeometry().right()-450, 20)
        self.build()
        self.hub.triggered.connect(self.on_hotkey)
        self.hub.stop_requested.connect(self.stop)
        self.hub.recorded.connect(self.on_recorded)
        self.hub.recording_finished.connect(self.record_finished)
        self.hub.checkpoint_requested.connect(self.record_checkboxes)
        self.hub.failed.connect(lambda message: QMessageBox.warning(self, '権限の確認', message))
        self.runner.progress.connect(self.on_progress)
        self.runner.finished.connect(self.run_finished)
        try:
            self.macros = self.store.load()
        except Exception as exc:
            QMessageBox.warning(self, '保存データを読み込めません', f'{exc}\n\n元のファイルを残しています。バックアップを読み込むか、別の保存先で起動してください。')
            self.store_failed = True
        else:
            self.store_failed = False
        self.refresh_macros()
        self.hub.set_bindings(self.macros)
        self.load_draft(None)
        if start_listeners:
            QTimer.singleShot(0, self.hub.start)

    def build(self):
        root = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 20)
        header = QHBoxLayout()
        title = label('かんたん操作ショートカット')
        title.setObjectName('title')
        title.setWordWrap(False)
        header.addWidget(title)
        header.addStretch()
        self.armed = QCheckBox('登録キーでの実行を有効にする')
        self.armed.toggled.connect(self.arm_changed)
        header.addWidget(self.armed)
        layout.addLayout(header)
        layout.addWidget(label('動きを登録すると、好きなキーでまとめて実行できます。停止するときは Esc。', True))
        split = QSplitter()
        layout.addWidget(split, 1)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 12, 12, 0)
        self.count = label('登録した操作 0 / 100')
        left_layout.addWidget(self.count)
        self.search = QLineEdit()
        self.search.setPlaceholderText('操作の名前で探す')
        self.search.textChanged.connect(self.filter_macros)
        left_layout.addWidget(self.search)
        self.macro_list = QListWidget()
        self.macro_list.currentRowChanged.connect(self.select_macro)
        left_layout.addWidget(self.macro_list, 1)
        left_layout.addWidget(button('＋ 新しい操作を登録', self.new, True))
        left_layout.addWidget(button('スクショのひな形', self.screenshot_template))
        left_layout.addWidget(button('Chrome履歴削除の登録案内', self.history_template))
        row = QHBoxLayout()
        row.addWidget(button('書き出す', self.export_data))
        row.addWidget(button('読み込む', self.import_data))
        left_layout.addLayout(row)
        split.addWidget(left)
        self.editor = QFrame()
        self.editor.setObjectName('card')
        editor_layout = QVBoxLayout(self.editor)
        editor_layout.setContentsMargins(20, 18, 20, 18)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setMaxLength(80)
        self.name.setPlaceholderText('例：お知らせを入力する')
        self.hotkey = KeyBox()
        self.enabled = QCheckBox('この操作を有効にする')
        form.addRow('① 操作の名前', self.name)
        form.addRow('② 呼び出すキー', self.hotkey)
        form.addRow('', self.enabled)
        editor_layout.addLayout(form)
        editor_layout.addWidget(label('③ 実行する動きを、上から順に並べます。'))
        self.steps = QListWidget()
        self.steps.itemDoubleClicked.connect(lambda _: self.edit_step())
        editor_layout.addWidget(self.steps, 1)
        row = QHBoxLayout()
        row.addWidget(button('＋ 動きを追加', self.add_step, True))
        row.addWidget(button('実際の操作を記録', self.begin_record))
        row.addWidget(button('変更', self.edit_step))
        row.addWidget(button('取り除く', self.remove_step))
        editor_layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(button('↑ 上に移動', lambda: self.move_step(-1)))
        row.addWidget(button('↓ 下に移動', lambda: self.move_step(1)))
        row.addWidget(button('選んだ動きだけ試す', lambda: self.run(single=True)))
        row.addStretch()
        editor_layout.addLayout(row)
        editor_layout.addWidget(label('場所を選ぶ／画像で探す／ボタンを認識する、の3通りで対象を指定できます。対象のアプリは実行時に手前に出してください。', True))
        row = QHBoxLayout()
        row.addWidget(button('保存する', self.save, True))
        row.addWidget(button('全部試す', self.run))
        row.addStretch()
        row.addWidget(button('複製', self.duplicate))
        row.addWidget(button('登録を削除', self.delete, danger=True))
        editor_layout.addLayout(row)
        split.addWidget(self.editor)
        split.setSizes([290, 830])
        footer = QHBoxLayout()
        self.status = label('「新しい操作を登録」から始めてください。', True)
        footer.addWidget(self.status, 1)
        self.stop_button = button('■ 停止（Esc）', self.stop, danger=True)
        self.stop_button.setEnabled(False)
        footer.addWidget(self.stop_button)
        footer.addWidget(button('使い方・Macの権限', self.help_dialog))
        layout.addLayout(footer)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.name.textChanged.connect(self.mark_dirty)
        self.hotkey.textChanged.connect(self.mark_dirty)
        self.enabled.toggled.connect(self.mark_dirty)

    def mark_dirty(self, *_):
        if not self.loading and self.draft is not None:
            self.dirty = True

    def arm_changed(self, enabled):
        self.hub.suspended = not enabled or self.runner.busy or self.hub.recording

    def refresh_macros(self):
        self.macro_list.blockSignals(True)
        self.macro_list.clear()
        for macro in self.macros:
            self.macro_list.addItem(f"{macro['name']}\n{macro['hotkey'].upper()}{' ／ 無効' if not macro['enabled'] else ''}")
            self.macro_list.item(self.macro_list.count()-1).setData(Qt.ItemDataRole.UserRole, macro['id'])
        self.macro_list.blockSignals(False)
        self.count.setText(f'登録した操作 {len(self.macros)} / {MAX_MACROS}')
        self.filter_macros()

    def filter_macros(self):
        query = self.search.text().casefold()
        for index in range(self.macro_list.count()):
            item = self.macro_list.item(index)
            item.setHidden(query not in item.text().casefold())

    def can_leave(self):
        if self.runner.busy or self.hub.recording or self.record_pending:
            return False
        if not self.dirty:
            return True
        answer = QMessageBox.question(self, '保存していない変更', '変更を保存してから移動しますか？',
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        return self.save() if answer == QMessageBox.StandardButton.Save else True

    def select_macro(self, row):
        if row < 0:
            return
        mid = self.macro_list.item(row).data(Qt.ItemDataRole.UserRole)
        if mid == self.current_id:
            return
        if not self.can_leave():
            self.macro_list.blockSignals(True)
            for index in range(self.macro_list.count()):
                if self.macro_list.item(index).data(Qt.ItemDataRole.UserRole) == self.current_id:
                    self.macro_list.setCurrentRow(index)
                    break
            self.macro_list.blockSignals(False)
            return
        self.load_draft(next(m for m in self.macros if m['id'] == mid))

    def load_draft(self, macro):
        self.loading = True
        self.draft = copy.deepcopy(macro) if macro else None
        self.current_id = macro['id'] if macro else None
        self.name.setText(macro['name'] if macro else '')
        self.hotkey.setText(macro['hotkey'] if macro else '')
        self.enabled.setChecked(macro['enabled'] if macro else True)
        self.refresh_steps()
        self.editor.setEnabled(macro is not None)
        self.loading = False
        self.dirty = False

    def refresh_steps(self, selected=None):
        self.steps.clear()
        if self.draft:
            for index, step in enumerate(self.draft['steps'], 1):
                self.steps.addItem(f'{index:02d}   {describe(step)}')
        if selected is not None:
            self.steps.setCurrentRow(selected)

    def new(self):
        if not self.can_leave():
            return False
        if len(self.macros) >= MAX_MACROS:
            QMessageBox.information(self, '登録数の上限', '登録できる操作は100件までです。不要な登録を削除してください。')
            return False
        self.load_draft(new_macro())
        self.dirty = True
        self.name.setFocus()
        self.status.setText('名前とキーを決めて、動きを追加してください。')
        return True

    def screenshot_template(self):
        if self.new():
            self.name.setText('スクリーンショットを保存')
            self.draft['steps'] = [{'kind': 'screenshot', 'folder': pictures_folder()}]
            self.refresh_steps()

    def history_template(self):
        guide = HistoryGuide(self)
        self.hub.suspended = True
        accepted = guide.exec() == QDialog.DialogCode.Accepted
        self.arm_changed(self.armed.isChecked())
        if accepted and self.new():
            self.name.setText('Chrome履歴削除：' + guide.period.currentText())
            self.draft['note'] = guide.instructions()
            self.begin_record(guide=guide.instructions())

    def _step_dialog(self, existing=None):
        self.hub.suspended = True
        dialog = StepDialog(self, existing)
        result = dialog.exec()
        self.arm_changed(self.armed.isChecked())
        return dialog.result_step if result == QDialog.DialogCode.Accepted else None

    def add_step(self):
        result = self._step_dialog()
        if result:
            self.draft['steps'].append(result)
            self.dirty = True
            self.refresh_steps(len(self.draft['steps'])-1)

    def edit_step(self):
        index = self.steps.currentRow()
        if index < 0:
            self.status.setText('変更する動きを選んでください。')
            return
        result = self._step_dialog(self.draft['steps'][index])
        if result:
            self.draft['steps'][index] = result
            self.dirty = True
            self.refresh_steps(index)

    def remove_step(self):
        index = self.steps.currentRow()
        if index >= 0:
            self.draft['steps'].pop(index)
            self.dirty = True
            self.refresh_steps(max(0, index-1))

    def move_step(self, direction):
        index = self.steps.currentRow()
        destination = index + direction
        if 0 <= index and 0 <= destination < len(self.draft['steps']):
            self.draft['steps'][index], self.draft['steps'][destination] = self.draft['steps'][destination], self.draft['steps'][index]
            self.dirty = True
            self.refresh_steps(destination)

    def snapshot(self):
        result = copy.deepcopy(self.draft)
        result.update(name=self.name.text().strip(), hotkey=normalize_hotkey(self.hotkey.text()), enabled=self.enabled.isChecked())
        return result

    def persist(self, macros):
        if self.store_failed:
            raise ValueError('元の保存データを保護しています。読み込み機能で正常なデータを復元するか、別の保存先を指定して再起動してください。')
        self.store.save(macros)
        self.macros = macros
        self.hub.set_bindings(macros)
        self.refresh_macros()

    def save(self):
        if self.draft is None:
            return False
        try:
            result = self.snapshot()
            updated = [copy.deepcopy(m) for m in self.macros if m['id'] != result['id']]
            updated.append(result)
            self.persist(updated)
            self.load_draft(result)
            self.status.setText('保存しました。「登録キーでの実行を有効にする」をオンにすると使えます。')
            return True
        except Exception as exc:
            QMessageBox.warning(self, '保存できません', str(exc))
            return False

    def duplicate(self):
        if not self.can_leave() or len(self.macros) >= MAX_MACROS:
            return
        source = copy.deepcopy(self.draft)
        source.update(id=uuid.uuid4().hex, name=source['name'][:75]+' のコピー', hotkey='')
        self.load_draft(source)
        self.dirty = True
        self.status.setText('コピー用の別のキーを指定して保存してください。')

    def delete(self):
        if not self.draft:
            return
        if QMessageBox.question(self, '登録を削除', 'この操作の登録を削除しますか？') != QMessageBox.StandardButton.Yes:
            return
        try:
            self.persist([m for m in self.macros if m['id'] != self.draft['id']])
            self.load_draft(None)
        except Exception as exc:
            QMessageBox.warning(self, '削除できません', str(exc))

    def begin_record(self, checked=False, guide=''):
        if self.runner.busy or self.hub.recording or self.record_pending:
            return
        if len(self.draft['steps']) > 1850:
            QMessageBox.information(self, '手順の上限', '記録を追加するため、先に不要な手順を取り除いてください。1つの操作は2000手順までです。')
            return
        message = ('4秒後に記録を始めます。対象のアプリを操作してください。\n'
                   'F8 または Esc で記録を止めます。文字入力も記録されるので、パスワードは入力しないでください。\n'
                   '記録した動きは今の手順の最後に追加します。')
        if guide:
            message += '\n\n' + guide
        if QMessageBox.information(self, '操作の記録', message, QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Ok:
            return
        self.recording_steps = []
        self.record_pending = True
        self.armed_before = self.armed.isChecked()
        self.hub.suspended = True
        self.editor.setEnabled(False)
        self.hide()
        self.capture_notice.setText('4秒後に記録開始。F8 または Esc で止めます。' + ('\n' + guide if guide else ''))
        self.capture_notice.show()
        def begin():
            if not self.record_pending:
                return
            self.record_pending = False
            self.capture_notice.setText('● 操作を記録中。F8 または Esc で終了。' + ('\n' + guide if guide else ''))
            self.hub.begin_record(limit=1900-len(self.draft['steps']))
        QTimer.singleShot(4000, begin)

    def on_recorded(self, step):
        self.recording_steps.append(step)

    def record_checkboxes(self):
        try:
            items = checkbox_snapshot()
            if len(items) + len(self.recording_steps) + len(self.draft['steps']) > 1900:
                raise ValueError('手順の上限に達しました。')
            self.recording_steps.extend(items)
            self.capture_notice.setText(f'● {len(items)}個のチェック状態を記録しました。F8 または Esc で終了。')
        except Exception as exc:
            self.capture_notice.setText(f'チェック状態を記録できません：{exc}\nF8で終了して、手動で動きを追加できます。')

    def record_finished(self):
        self.capture_notice.hide()
        self.draft['steps'].extend(self.recording_steps)
        self.recording_steps = []
        self.dirty = True
        self.editor.setEnabled(True)
        self.refresh_steps()
        self.show()
        self.activateWindow()
        self.arm_changed(self.armed_before)
        self.status.setText('記録しました。不要な動きを取り除き、名前とキーを指定して保存してください。')

    def run(self, checked=False, single=False):
        if not self.draft or self.runner.busy:
            return
        steps = self.draft['steps']
        if single:
            index = self.steps.currentRow()
            if index < 0:
                self.status.setText('試す動きを選んでください。')
                return
            steps = [steps[index]]
        if not steps:
            self.status.setText('まず動きを追加してください。')
            return
        self.start_run(steps)

    def on_hotkey(self, mid):
        # Editing a registration must not trigger automation from its key field.
        if self.isActiveWindow() or QApplication.activeModalWidget() or self.runner.busy or self.hub.recording:
            return
        macro = next((m for m in self.macros if m['id'] == mid), None)
        if macro and self.armed.isChecked():
            self.start_run(macro['steps'])

    def start_run(self, steps):
        try:
            if self.runner.start(steps):
                self.editor.setEnabled(False)
                self.macro_list.setEnabled(False)
                self.armed.setEnabled(False)
                self.stop_button.setEnabled(True)
                self.progress.setRange(0, len(steps))
                self.progress.setValue(0)
        except Exception as exc:
            QMessageBox.warning(self, '実行できません', str(exc))

    def on_progress(self, index, message):
        self.status.setText(message)
        if index >= 0:
            self.steps.setCurrentRow(index)
            self.progress.setValue(index+1)

    def run_finished(self, message, ok):
        self.status.setText(message)
        self.editor.setEnabled(self.draft is not None)
        self.macro_list.setEnabled(True)
        self.armed.setEnabled(True)
        self.stop_button.setEnabled(False)
        # Thread may be alive until this signal is delivered. Defer rearming.
        QTimer.singleShot(100, lambda: self.arm_changed(self.armed.isChecked()))

    def stop(self):
        self.runner.stop()
        if self.record_pending:
            self.record_pending = False
            self.capture_notice.hide()
            self.editor.setEnabled(True)
            self.show()
            self.arm_changed(self.armed_before)
            self.status.setText('記録を開始せずに戻りました。')
        if self.hub.recording:
            self.hub.end_record()

    def export_data(self):
        path, _ = QFileDialog.getSaveFileName(self, '登録を書き出す', '操作の登録.json', 'JSON (*.json)')
        if path:
            try:
                Store(path).save(self.macros)
                self.status.setText('保存済みの登録を書き出しました。入力文章や画像も含まれます。')
            except Exception as exc:
                QMessageBox.warning(self, '書き出せません', str(exc))

    def import_data(self):
        if not self.can_leave():
            return
        path, _ = QFileDialog.getOpenFileName(self, '登録を読み込む', '', 'JSON (*.json)')
        if not path:
            return
        try:
            if Path(path).stat().st_size > 50_000_000:
                raise ValueError('読み込むファイルは50MBまでです。')
            imported = Store(path).load()
            for macro in imported:
                macro['enabled'] = False
            validate(imported)
            if QMessageBox.question(self, '登録を読み込む', f'{len(imported)}件で現在の登録を置き換えますか？\n現在の保存データはバックアップされます。読み込んだ登録は無効の状態で取り込みます。') != QMessageBox.StandardButton.Yes:
                return
            self.store.save(imported)
            self.store_failed = False
            self.macros = imported
            self.hub.set_bindings(imported)
            self.refresh_macros()
            self.load_draft(None)
        except Exception as exc:
            QMessageBox.warning(self, '読み込めません', str(exc))

    def help_dialog(self):
        QMessageBox.information(self, '使い方',
            '1. 新しい操作を登録し、名前と呼び出すキーを決めます。\n'
            '2. 動きを追加するか、実際の操作を記録します。\n'
            '3. 保存し、登録キーでの実行を有効にします。\n'
            '4. 対象アプリを手前に出し、登録したキーを押します。\n\n'
            'Escで停止、F8で記録終了、F7で現在のチェック状態を記録できます。\n'
            'アプリを閉じるとショートカットも停止します。\n\n'
            'Mac：システム設定 → プライバシーとセキュリティで、\n'
            'アクセシビリティ、入力監視、画面収録をこのアプリに許可してください。\n'
            '許可後にアプリを再起動してください。\n\n'
            '管理者として動くWindowsアプリや、認証・保護された画面は操作できない場合があります。\n'
            '対象画像やボタンは、画面やOSが変わった場合に選び直してください。\n\n'
            f'登録の保存先：{self.store.path}')

    def closeEvent(self, event):
        if self.runner.busy or self.hub.recording or self.record_pending:
            self.stop()
            self.status.setText('停止しています。少し待ってから閉じてください。')
            event.ignore()
            return
        if not self.can_leave():
            event.ignore()
            return
        self.hub.close()
        self.capture_notice.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('EasyActionHotkeys')
    app.setStyleSheet(STYLE)
    # Override is useful for portable deployments and verification.
    override = os.environ.get('EASY_ACTION_DATA')
    store = Store(Path(override) / 'actions.json') if override else Store()
    store.path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(store.path.parent / 'running.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock():
        QMessageBox.information(None, '起動済みです', 'すでに起動しています。同じ保存先で同時に2つ起動できません。')
        return
    window = MainWindow(store)
    window.show()
    sys.exit(app.exec())
