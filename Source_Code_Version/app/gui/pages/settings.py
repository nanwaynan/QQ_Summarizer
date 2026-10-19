"""设置页

包含：
- AI 配置（Base URL / API Key / 模型名）+ 测试连接
- QQ 账号选择（含切换）
- 群聊选择（多选 + 搜索）
- 存储路径
- 主题
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup, QFileDialog, QFormLayout, QHBoxLayout, QListWidget,
    QListWidgetItem, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, CheckBox, ComboBox,
    InfoBar, InfoBarPosition, LineEdit, PasswordLineEdit, PrimaryPushButton,
    PushButton, RadioButton, ScrollArea, StrongBodyLabel, SubtitleLabel,
    SwitchButton,
)

from ...config import Config, mask_key
from ...logger import Logger
from ..workers import (
    LoadAccountsWorker, LoadGroupsWorker, SwitchAccountWorker, TestAIWorker,
)


class SettingsPage(QWidget):
    """设置页。"""

    def __init__(self, config: Config, logger: Logger,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.logger = logger
        self.setObjectName('SettingsPage')

        self._workers: dict[str, object] = {}
        self._groups: list[dict] = []
        self._accounts: list[dict] = []

        self._build_ui()
        self.reload()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet('QScrollArea { border: none; background: transparent; }')

        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(24, 20, 24, 24)
        root.setSpacing(14)

        root.addWidget(SubtitleLabel('设置'))

        # ================= AI 配置 =================
        root.addWidget(StrongBodyLabel('AI 配置'))

        ai_card = CardWidget()
        ai_form = QFormLayout(ai_card)
        ai_form.setContentsMargins(20, 16, 20, 16)
        ai_form.setSpacing(10)

        self.in_base = LineEdit()
        self.in_base.setPlaceholderText('http://127.0.0.1:7863/v1')
        ai_form.addRow('接口地址', self.in_base)

        self.in_model = LineEdit()
        self.in_model.setPlaceholderText('hy3')
        ai_form.addRow('模型名称', self.in_model)

        key_row = QHBoxLayout()
        self.in_key = PasswordLineEdit()
        self.in_key.setPlaceholderText('sk-... 或本地服务的任意值')
        key_row.addWidget(self.in_key, 1)
        self.btn_show_key = CheckBox('显示')
        self.btn_show_key.stateChanged.connect(self._toggle_key_visible)
        key_row.addWidget(self.btn_show_key)
        key_wrap = QWidget()
        key_wrap.setLayout(key_row)
        ai_form.addRow('API Key', key_wrap)

        test_row = QHBoxLayout()
        self.btn_test = PushButton('测试连接')
        self.btn_test.clicked.connect(self.test_ai)
        test_row.addWidget(self.btn_test)
        self.test_result = CaptionLabel('')
        test_row.addWidget(self.test_result, 1)
        test_wrap = QWidget()
        test_wrap.setLayout(test_row)
        ai_form.addRow('', test_wrap)

        root.addWidget(ai_card)

        # ================= QQ 账号 =================
        root.addWidget(StrongBodyLabel('QQ 账号'))

        acc_card = CardWidget()
        acc_lay = QVBoxLayout(acc_card)
        acc_lay.setContentsMargins(20, 16, 20, 16)
        acc_lay.setSpacing(10)

        acc_top = QHBoxLayout()
        self.acc_status = BodyLabel('正在加载...')
        acc_top.addWidget(self.acc_status, 1)
        self.btn_reload_acc = PushButton('刷新列表')
        self.btn_reload_acc.clicked.connect(self.load_accounts)
        acc_top.addWidget(self.btn_reload_acc)
        acc_lay.addLayout(acc_top)

        self.acc_combo = ComboBox()
        self.acc_combo.setMinimumWidth(280)
        acc_lay.addWidget(self.acc_combo)

        self.btn_switch = PrimaryPushButton('切换到此账号')
        self.btn_switch.clicked.connect(self.switch_account)
        acc_lay.addWidget(self.btn_switch)

        self.acc_hint = CaptionLabel(
            '切换账号会重启 NapCat（约 1-3 分钟），期间服务不可用。\n'
            '群列表只对当前登录账号有效，切换后会自动刷新。')
        acc_lay.addWidget(self.acc_hint)

        root.addWidget(acc_card)

        # ================= 群聊选择 =================
        root.addWidget(StrongBodyLabel('群聊选择'))

        grp_card = CardWidget()
        grp_lay = QVBoxLayout(grp_card)
        grp_lay.setContentsMargins(20, 16, 20, 16)
        grp_lay.setSpacing(10)

        grp_top = QHBoxLayout()
        self.grp_status = BodyLabel('正在加载...')
        grp_top.addWidget(self.grp_status, 1)
        self.btn_reload_grp = PushButton('刷新列表')
        self.btn_reload_grp.clicked.connect(self.load_groups)
        grp_top.addWidget(self.btn_reload_grp)
        grp_lay.addLayout(grp_top)

        self.grp_search = LineEdit()
        self.grp_search.setPlaceholderText('搜索群名或群号...')
        self.grp_search.textChanged.connect(self._filter_groups)
        grp_lay.addWidget(self.grp_search)

        self.grp_list = QListWidget()
        self.grp_list.setMinimumHeight(180)
        self.grp_list.itemChanged.connect(self._on_group_item_changed)
        grp_lay.addWidget(self.grp_list)

        grp_btns = QHBoxLayout()
        btn_all = PushButton('全选')
        btn_all.clicked.connect(lambda: self._set_all_groups(True))
        grp_btns.addWidget(btn_all)
        btn_none = PushButton('全不选')
        btn_none.clicked.connect(lambda: self._set_all_groups(False))
        grp_btns.addWidget(btn_none)
        grp_btns.addStretch(1)
        grp_lay.addLayout(grp_btns)

        root.addWidget(grp_card)

        # ================= 存储与其他 =================
        root.addWidget(StrongBodyLabel('存储与其他'))

        misc_card = CardWidget()
        misc_form = QFormLayout(misc_card)
        misc_form.setContentsMargins(20, 16, 20, 16)
        misc_form.setSpacing(10)

        out_row = QHBoxLayout()
        self.in_output = LineEdit()
        out_row.addWidget(self.in_output, 1)
        btn_pick = PushButton('浏览')
        btn_pick.clicked.connect(self.pick_output_dir)
        out_row.addWidget(btn_pick)
        out_wrap = QWidget()
        out_wrap.setLayout(out_row)
        misc_form.addRow('输出目录', out_wrap)

        # ---- NapCat 目录（多处报错文案都提示"设置页可修改"，这里补上）----
        ng_row = QHBoxLayout()
        self.in_napcat_dir = LineEdit()
        self.in_napcat_dir.setPlaceholderText('留空 = 自动探测（程序目录下的 napcat_green）')
        ng_row.addWidget(self.in_napcat_dir, 1)
        btn_pick_ng = PushButton('浏览')
        btn_pick_ng.clicked.connect(self.pick_napcat_dir)
        ng_row.addWidget(btn_pick_ng)
        btn_auto_ng = PushButton('自动探测')
        btn_auto_ng.clicked.connect(self.auto_detect_napcat)
        ng_row.addWidget(btn_auto_ng)
        ng_wrap = QWidget()
        ng_wrap.setLayout(ng_row)
        misc_form.addRow('NapCat 目录', ng_wrap)

        self.ng_hint = CaptionLabel('')
        misc_form.addRow('', self.ng_hint)

        # ---- NapCat 启动超时（新机器首次启动可能很慢）----
        self.in_boot_timeout = LineEdit()
        self.in_boot_timeout.setPlaceholderText('180')
        misc_form.addRow('NapCat 启动超时（秒）', self.in_boot_timeout)

        # 总结模式：两个选项同时可见，比下拉框直观
        mode_row = QHBoxLayout()
        mode_row.setSpacing(16)
        self.rb_mode_image = RadioButton('文字 + 图片')
        self.rb_mode_text = RadioButton('仅文字')
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.rb_mode_image)
        self._mode_group.addButton(self.rb_mode_text)
        self.rb_mode_image.setChecked(True)
        mode_row.addWidget(self.rb_mode_image)
        mode_row.addWidget(self.rb_mode_text)
        mode_row.addStretch(1)
        mode_wrap = QWidget()
        mode_wrap.setLayout(mode_row)
        misc_form.addRow('总结模式', mode_wrap)

        self.mode_hint = CaptionLabel(
            '「文字 + 图片」会抓取群内图片并由 AI 甄选后附在总结末尾；'
            '「仅文字」跳过全部图片处理，更快。')
        misc_form.addRow('', self.mode_hint)

        self.sw_images = SwitchButton()
        self.sw_images.setOnText('开启')
        self.sw_images.setOffText('关闭')
        misc_form.addRow('保存图片', self.sw_images)

        self.in_max_images = LineEdit()
        self.in_max_images.setPlaceholderText('40')
        misc_form.addRow('每期最多图片数', self.in_max_images)

        self.in_min_score = LineEdit()
        self.in_min_score.setPlaceholderText('4')
        misc_form.addRow('图片最低分(1-5)', self.in_min_score)

        # 「仅文字」时这几项无意义，置灰避免误解
        self._img_rows: list[QWidget] = [
            self.sw_images, self.in_max_images, self.in_min_score]
        self.rb_mode_text.toggled.connect(self._on_mode_changed)
        self._on_mode_changed(self.rb_mode_text.isChecked())

        self.cmb_theme = ComboBox()
        self.cmb_theme.addItems(['跟随系统', '浅色', '深色'])
        misc_form.addRow('主题', self.cmb_theme)

        root.addWidget(misc_card)

        # ================= 保存 =================
        save_row = QHBoxLayout()
        self.btn_save = PrimaryPushButton('保存设置')
        self.btn_save.setMinimumWidth(140)
        self.btn_save.clicked.connect(self.save_settings)
        save_row.addWidget(self.btn_save)
        save_row.addStretch(1)
        root.addLayout(save_row)

        root.addStretch(1)
        scroll.setWidget(container)
        outer.addWidget(scroll)

    # ---------- 加载/保存 ----------

    def _on_mode_changed(self, text_only: bool) -> None:
        """仅文字模式下，图片相关选项无意义，置灰。"""
        for w in self._img_rows:
            w.setEnabled(not text_only)

    def reload(self) -> None:
        """从配置刷新界面。"""
        c = self.config
        self.in_base.setText(str(c.get('ai_base', '')))
        self.in_model.setText(str(c.get('ai_model', '')))
        self.in_key.setText(str(c.get('ai_key', '')))
        self.in_output.setText(str(c.get('output_dir', '')))
        self.in_max_images.setText(str(c.get('max_images', 40)))
        self.in_min_score.setText(str(c.get('img_min_score', 4)))
        self.sw_images.setChecked(bool(c.get('save_images', True)))

        # 总结模式
        mode = str(c.get('summary_mode', 'text_image')).lower()
        if mode == 'text_only':
            self.rb_mode_text.setChecked(True)
        else:
            self.rb_mode_image.setChecked(True)
        self._on_mode_changed(self.rb_mode_text.isChecked())

        theme = str(c.get('theme', 'auto')).lower()
        self.cmb_theme.setCurrentIndex(
            {'auto': 0, 'light': 1, 'dark': 2}.get(theme, 0))

        # 群选择恢复
        selected = set(str(x) for x in (c.get('group_ids') or []))
        single = str(c.get('group_id') or '').strip()
        if single:
            selected.add(single)
        self._selected_groups = selected
        self._render_groups()

        # 账号
        cur = str(c.get('qq_account') or '').strip()
        if cur:
            self.acc_status.setText(f'当前账号：{cur}')

        # NapCat 目录与启动超时
        self.in_napcat_dir.setText(str(c.get('napcat_dir') or ''))
        self.in_boot_timeout.setText(str(c.get('napcat_boot_timeout', 180) or 180))
        self._refresh_ng_hint()

        # 记录"已保存状态"的快照，用于判断有没有未保存的修改
        self._saved_snapshot = self._current_snapshot()
        self._dirty = False

    # ---------- 未保存检测 ----------

    def _current_snapshot(self) -> tuple:
        """把当前界面值拍成快照（用于比较是否改动过）。"""
        try:
            theme_idx = self.cmb_theme.currentIndex()
        except Exception:
            theme_idx = 0
        return (
            self.in_base.text().strip(),
            self.in_model.text().strip(),
            self.in_key.text().strip(),
            self.in_output.text().strip(),
            self.in_max_images.text().strip(),
            self.in_min_score.text().strip(),
            self.sw_images.isChecked(),
            self.rb_mode_text.isChecked(),
            theme_idx,
            self.in_napcat_dir.text().strip(),
            self.in_boot_timeout.text().strip(),
            tuple(sorted(self._selected_groups)),
            self.acc_combo.currentIndex() if self._accounts else -1,
        )

    def has_unsaved_changes(self) -> bool:
        """是否有未保存的修改。"""
        saved = getattr(self, '_saved_snapshot', None)
        if saved is None:
            return False
        return self._current_snapshot() != saved

    # ---------- NapCat 目录 ----------

    def pick_napcat_dir(self) -> None:
        """手动选择 NapCat 目录。"""
        from PySide6.QtWidgets import QFileDialog
        start = self.in_napcat_dir.text().strip() or str(self.config.get('napcat_dir') or '')
        d = QFileDialog.getExistingDirectory(self, '选择 NapCat 目录', start)
        if d:
            self.in_napcat_dir.setText(d)
            self._refresh_ng_hint()

    def auto_detect_napcat(self) -> None:
        """重新自动探测 NapCat 目录。"""
        from ...config import detect_napcat_dir
        found = detect_napcat_dir()
        if found:
            self.in_napcat_dir.setText(found)
            self._refresh_ng_hint()
            InfoBar.success('已找到', found, duration=4000,
                            position=InfoBarPosition.TOP_RIGHT, parent=self)
        else:
            self.in_napcat_dir.setText('')
            self._refresh_ng_hint()
            InfoBar.warning(
                '未找到 NapCat',
                '请确认 napcat_green 文件夹在程序目录下，或手动选择目录',
                duration=6000, position=InfoBarPosition.TOP_RIGHT, parent=self)

    def _refresh_ng_hint(self) -> None:
        """检查当前输入的 NapCat 目录是否有效，并显示提示。"""
        raw = self.in_napcat_dir.text().strip()
        if not raw:
            self.ng_hint.setText('留空时会自动探测：程序目录 → 上级目录 → C:\\NapCat')
            self.ng_hint.setTextColor('#9d5d00', '#ffd166')
            return

        from pathlib import Path
        d = Path(raw)
        if not d.is_dir():
            self.ng_hint.setText('⚠ 该目录不存在')
            self.ng_hint.setTextColor('#c42b1c', '#ff99a4')
            return

        needed = ('NapCatWinBootMain.exe', 'NapCatWinBootHook.dll', 'napcat.mjs')
        missing = [n for n in needed if not (d / n).exists()]
        if missing:
            self.ng_hint.setText(f'⚠ 目录不完整，缺少：{", ".join(missing)}')
            self.ng_hint.setTextColor('#c42b1c', '#ff99a4')
        else:
            bat = None
            for name in ('start.bat', '启动NapCat.bat'):
                if (d / name).exists():
                    bat = name
                    break
            self.ng_hint.setText(
                f'✓ 有效目录（启动脚本：{bat or "未找到"}）')
            self.ng_hint.setTextColor('#107c10', '#6ccb5f')

    def save_settings(self) -> None:
        c = self.config
        c.set('ai_base', self.in_base.text().strip())
        c.set('ai_model', self.in_model.text().strip())
        c.set('ai_key', self.in_key.text().strip())
        c.set('output_dir', self.in_output.text().strip())

        try:
            c.set('max_images', max(1, int(self.in_max_images.text() or 40)))
        except ValueError:
            c.set('max_images', 40)
        try:
            v = int(self.in_min_score.text() or 4)
            c.set('img_min_score', min(5, max(1, v)))
        except ValueError:
            c.set('img_min_score', 4)

        c.set('save_images', self.sw_images.isChecked())
        c.set('summary_mode',
              'text_only' if self.rb_mode_text.isChecked() else 'text_image')
        c.set('theme', ['auto', 'light', 'dark'][self.cmb_theme.currentIndex()])

        # NapCat 目录与启动超时
        ng_dir = self.in_napcat_dir.text().strip()
        c.set('napcat_dir', ng_dir)
        try:
            bt = int(self.in_boot_timeout.text() or 180)
            c.set('napcat_boot_timeout', max(30, min(600, bt)))
        except ValueError:
            c.set('napcat_boot_timeout', 180)

        # 群选择：选中项写入 group_ids，第一个同步到 group_id/group_name
        ids = sorted(self._selected_groups)
        c.set('group_ids', ids)
        if ids:
            c.set('group_id', ids[0])
            for g in self._groups:
                if g['group_id'] == ids[0]:
                    c.set('group_name', g['group_name'])
                    break

        # 账号
        idx = self.acc_combo.currentIndex()
        if 0 <= idx < len(self._accounts):
            c.set('qq_account', self._accounts[idx]['uin'])

        try:
            c.save()
        except Exception as e:
            InfoBar.error('保存失败', str(e)[:200], duration=5000,
                          position=InfoBarPosition.TOP, parent=self)
            return

        # 让日志知道新的 key（用于打码）
        self.logger.register_secret(str(c.get('ai_key', '')))

        # 保存成功：更新"已保存"快照，清除未保存标记
        self._saved_snapshot = self._current_snapshot()
        self._dirty = False

        # 通知主窗口切主题
        w = self.window()
        if hasattr(w, 'apply_theme'):
            try:
                w.apply_theme()
            except Exception:
                pass

        InfoBar.success(
            '已保存', '设置已生效（无需重启程序）',
            duration=3000, position=InfoBarPosition.TOP_RIGHT, parent=self)

    # ---------- AI 测试 ----------

    def _toggle_key_visible(self, state: int) -> None:
        # 先保存当前输入，避免切换时丢失
        cur = self.in_key.text()
        if state:
            self.in_key.setEchoMode(LineEdit.EchoMode.Normal)
        else:
            self.in_key.setEchoMode(LineEdit.EchoMode.Password)
        self.in_key.setText(cur)

    def test_ai(self) -> None:
        # 用界面上的值临时测试（不必先保存）
        snapshot = {k: self.config.get(k) for k in
                    ('ai_base', 'ai_model', 'ai_key')}
        self.config.set('ai_base', self.in_base.text().strip())
        self.config.set('ai_model', self.in_model.text().strip())
        self.config.set('ai_key', self.in_key.text().strip())

        self.btn_test.setEnabled(False)
        self.test_result.setText('测试中...')

        w = TestAIWorker(self.config)
        self._workers['test'] = w

        def _done(result: object) -> None:
            r = result if isinstance(result, dict) else {}
            if r.get('ok'):
                self.test_result.setText(
                    f'✓ 连接成功 · {r.get("latency", 0):.2f}s · '
                    f'回复：{r.get("reply", "")[:30]}')
                self.test_result.setTextColor('#107c10', '#6ccb5f')
            else:
                self.test_result.setText(f'✗ {str(r.get("error", ""))[:160]}')
                self.test_result.setTextColor('#c42b1c', '#ff99a4')

        def _failed(msg: str) -> None:
            self.test_result.setText(f'✗ {msg[:160]}')
            self.test_result.setTextColor('#c42b1c', '#ff99a4')

        def _finish() -> None:
            self.btn_test.setEnabled(True)
            # 还原配置（未保存就不生效）
            for k, v in snapshot.items():
                self.config.set(k, v)

        w.finished_ok.connect(_done)
        w.failed.connect(_failed)
        w.done.connect(_finish)
        w.start()

    # ---------- 账号 ----------

    def load_accounts(self) -> None:
        self.acc_status.setText('正在加载账号列表...')
        self.btn_reload_acc.setEnabled(False)

        w = LoadAccountsWorker(self.config)
        self._workers['accounts'] = w

        def _ok(data: object) -> None:
            accounts = data if isinstance(data, list) else []
            self._accounts = accounts
            self.acc_combo.clear()
            for a in accounts:
                nick = a.get('nickname') or ''
                label = f'{a["uin"]}  {nick}' if nick else a['uin']
                self.acc_combo.addItem(label)
            if accounts:
                cur = str(self.config.get('qq_account') or '').strip()
                for i, a in enumerate(accounts):
                    if a['uin'] == cur:
                        self.acc_combo.setCurrentIndex(i)
                        break
                self.acc_status.setText(f'共 {len(accounts)} 个可用账号')
            else:
                self.acc_status.setText('未获取到账号（NapCat 可能未运行）')

        def _fail(msg: str) -> None:
            self.acc_status.setText(f'加载失败：{msg[:80]}')

        def _finish() -> None:
            self.btn_reload_acc.setEnabled(True)

        w.finished_ok.connect(_ok)
        w.failed.connect(_fail)
        w.done.connect(_finish)
        w.start()

    def switch_account(self) -> None:
        idx = self.acc_combo.currentIndex()
        if not (0 <= idx < len(self._accounts)):
            InfoBar.warning('未选择账号', '请先刷新账号列表并选择一个账号',
                            duration=3000, position=InfoBarPosition.TOP,
                            parent=self)
            return
        uin = self._accounts[idx]['uin']
        cur = str(self.config.get('qq_account') or '').strip()
        if uin == cur:
            InfoBar.info('无需切换', f'当前已经是账号 {uin}',
                         duration=2500, position=InfoBarPosition.TOP, parent=self)
            return

        from qfluentwidgets import MessageBox
        box = MessageBox(
            '确认切换账号',
            f'将切换到账号 {uin}。\n\n'
            f'这会重启 NapCat 服务（约 1-3 分钟），期间无法总结。\n'
            f'是否需要继续？',
            self.window())
        if not box.exec():
            return

        self.btn_switch.setEnabled(False)
        self.acc_status.setText(f'正在切换到 {uin}...')

        w = SwitchAccountWorker(self.config, uin)
        self._workers['switch'] = w

        def _ok(info: object) -> None:
            d = info if isinstance(info, dict) else {}
            nick = d.get('nickname', '')
            uid = d.get('user_id', '')
            self.config.set('qq_account', str(uid or uin))
            try:
                self.config.save()
            except Exception:
                pass
            self.acc_status.setText(f'已切换到 {nick} ({uid})')
            InfoBar.success('切换成功', f'当前账号：{nick} ({uid})',
                            duration=4000, position=InfoBarPosition.TOP_RIGHT,
                            parent=self)
            QTimer.singleShot(500, self.load_groups)

        def _fail(msg: str) -> None:
            self.acc_status.setText(f'切换失败：{msg[:100]}')
            InfoBar.error('切换失败', msg[:250], duration=8000,
                          position=InfoBarPosition.TOP_RIGHT, parent=self)

        w.finished_ok.connect(_ok)
        w.failed.connect(_fail)
        w.done.connect(lambda: self.btn_switch.setEnabled(True))
        w.start()

    # ---------- 群列表 ----------

    def load_groups(self) -> None:
        self.grp_status.setText('正在加载群列表...')
        self.btn_reload_grp.setEnabled(False)

        w = LoadGroupsWorker(self.config)
        self._workers['groups'] = w

        def _ok(data: object) -> None:
            d = data if isinstance(data, dict) else {}
            self._groups = d.get('groups') or []
            login = d.get('login') or {}
            nick = login.get('nickname', '')
            self.grp_status.setText(
                f'共 {len(self._groups)} 个群（账号 {nick}）')
            self._render_groups()

        def _fail(msg: str) -> None:
            self.grp_status.setText(f'加载失败：{msg[:80]}')

        def _finish() -> None:
            self.btn_reload_grp.setEnabled(True)

        w.finished_ok.connect(_ok)
        w.failed.connect(_fail)
        w.done.connect(_finish)
        w.start()

    def _render_groups(self) -> None:
        """按当前选择状态渲染群列表。"""
        if not hasattr(self, '_selected_groups'):
            self._selected_groups: set[str] = set()

        self.grp_list.blockSignals(True)
        self.grp_list.clear()
        for g in self._groups:
            gid = g['group_id']
            text = f'{g["group_name"]}  ({gid})  ·  {g.get("member_count", 0)} 人'
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if gid in self._selected_groups
                else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, gid)
            self.grp_list.addItem(item)
        self.grp_list.blockSignals(False)

    def _filter_groups(self, text: str) -> None:
        key = text.strip().lower()
        for i in range(self.grp_list.count()):
            item = self.grp_list.item(i)
            item.setHidden(bool(key) and key not in item.text().lower())

    def _set_all_groups(self, checked: bool) -> None:
        self.grp_list.blockSignals(True)
        for i in range(self.grp_list.count()):
            item = self.grp_list.item(i)
            if not item.isHidden():
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                gid = item.data(Qt.ItemDataRole.UserRole)
                if checked:
                    self._selected_groups.add(gid)
                else:
                    self._selected_groups.discard(gid)
        self.grp_list.blockSignals(False)

    def _on_group_item_changed(self, item: QListWidgetItem) -> None:
        gid = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_groups.add(gid)
        else:
            self._selected_groups.discard(gid)

    # ---------- 其他 ----------

    def pick_output_dir(self) -> None:
        cur = self.in_output.text().strip() or str(Config().output_path)
        d = QFileDialog.getExistingDirectory(self, '选择输出目录', cur)
        if d:
            self.in_output.setText(d)
