"""PyQt6 悬浮字幕（阶段 4，可选组件）。

入口：
    python -m fr_translator.gui

复用阶段 1 的 audio_capture / vad / asr / translate / pipeline，不重复实现识别逻辑。
设计：
    * 无边框、半透明、始终置顶的悬浮窗，可拖动；
    * 右键菜单：显示/隐藏法语原文、字号、点击穿透、退出；
    * 识别与翻译在 QThread 中运行，通过 Qt 信号回到 UI 线程；
    * 窗口位置和字号写入 %LOCALAPPDATA%\\fr-translator\\config.yaml。
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading

from . import __version__

try:
    from PyQt6.QtCore import QObject, QPoint, Qt, QThread, QTimer, pyqtSignal
    from PyQt6.QtGui import QAction, QColor, QFont, QGuiApplication, QPainter, QPen
    from PyQt6.QtWidgets import (
        QApplication,
        QLabel,
        QMenu,
        QMessageBox,
        QStyle,
        QSystemTrayIcon,
        QVBoxLayout,
        QWidget,
    )
except ImportError:  # pragma: no cover - 缺少可选依赖时给出中文提示
    print(
        "缺少 PyQt6，无法启动悬浮字幕。\n"
        "请执行：pip install -r requirements-gui.txt\n"
        "便携包请使用 -WithGui 参数重新构建。"
    )
    raise SystemExit(1)

from .audio_capture import LoopbackCapture
from .config import (
    AppConfig,
    AppPaths,
    detect_edition,
    load_config,
    resolve_offline,
    update_user_config,
)
from .devices import list_loopback_devices, resolve_capture_device
from .errors import FrTranslatorError
from .pipeline import TranslationEngine

logger = logging.getLogger("fr_translator.gui")


# ---------------------------------------------------------------------------
# 线程间桥接
# ---------------------------------------------------------------------------
class Bridge(QObject):
    status = pyqtSignal(str, str)
    caption = pyqtSignal(str, str)
    translation_failed = pyqtSignal(str, str)
    fatal = pyqtSignal(str)


class _SinkAdapter:
    """把 Bridge 适配成 pipeline.EventSink（信号发射本身是线程安全的）。"""

    def __init__(self, bridge: Bridge) -> None:
        self._bridge = bridge

    def status(self, message: str, level: str = "info") -> None:
        self._bridge.status.emit(message, level)

    def caption(self, french: str, chinese: str) -> None:
        self._bridge.caption.emit(french, chinese)

    def translation_failed(self, french: str, reason: str = "") -> None:
        self._bridge.translation_failed.emit(french, reason)


class PipelineWorker(QThread):
    def __init__(self, engine: TranslationEngine, bridge: Bridge) -> None:
        super().__init__()
        self._engine = engine
        self._bridge = bridge
        self._stop = threading.Event()

    def run(self) -> None:  # noqa: D401
        try:
            self._engine.run(self._stop)
        except FrTranslatorError as exc:
            logger.error("GUI 引擎错误：%s", exc)
            self._bridge.fatal.emit(str(exc))
        except Exception as exc:  # pragma: no cover
            logger.exception("GUI 未预期错误")
            self._bridge.fatal.emit(f"程序遇到未预期的错误：{exc}")
        finally:
            self._stop.set()

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# 悬浮窗
# ---------------------------------------------------------------------------
class Overlay(QWidget):
    def __init__(self, config: AppConfig, paths: AppPaths) -> None:
        super().__init__()
        self._paths = paths
        gui_cfg = dict((config.raw.get("gui") or {})) if isinstance(config.raw.get("gui"), dict) else {}
        self._font_size = int(gui_cfg.get("font_size", 22) or 22)
        self._show_french = bool(gui_cfg.get("show_french", True))
        self._click_through = bool(gui_cfg.get("click_through", False))
        self._drag_offset: QPoint | None = None
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._clear_status)
        self._tray: QSystemTrayIcon | None = None

        self.setWindowTitle("fr-translator 悬浮字幕")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(640, 130)
        self.resize(int(gui_cfg.get("width", 960) or 960), 190)

        self._french = QLabel("")
        self._french.setWordWrap(True)
        self._french.setStyleSheet("color: rgba(220,230,240,190);")
        self._french.setFont(QFont("Microsoft YaHei UI", max(10, self._font_size - 8)))

        self._chinese = QLabel("等待字幕…")
        self._chinese.setWordWrap(True)
        self._chinese.setStyleSheet("color: white;")
        self._chinese.setFont(QFont("Microsoft YaHei UI", self._font_size, QFont.Weight.DemiBold))

        self._status = QLabel("正在启动…")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: rgba(255,200,120,220);")
        self._status.setFont(QFont("Microsoft YaHei UI", max(9, self._font_size - 11)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 12)
        layout.setSpacing(6)
        layout.addWidget(self._french)
        layout.addWidget(self._chinese, 1)
        layout.addWidget(self._status)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

        self._restore_position(gui_cfg)
        self._setup_tray()
        self._apply_click_through(self._click_through)

    # -- 外观 --------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(18, 20, 26, 205))
        painter.setPen(QPen(QColor(255, 255, 255, 35), 1))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 16, 16)

    def _setup_tray(self) -> None:
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("fr-translator 法语实时翻译")
        menu = QMenu()
        show_action = QAction("显示字幕窗口", self)
        show_action.triggered.connect(self._show_window)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason) -> None:  # noqa: ANN001
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _restore_position(self, gui_cfg: dict) -> None:
        screen = QGuiApplication.primaryScreen()
        geometry = screen.availableGeometry() if screen else None
        x = gui_cfg.get("x")
        y = gui_cfg.get("y")
        if isinstance(x, int) and isinstance(y, int) and geometry is not None:
            # 防止换显示器后窗口跑到屏幕外
            if geometry.contains(QPoint(x + 50, y + 50)):
                self.move(x, y)
                return
        if geometry is not None:
            self.move(
                geometry.center().x() - self.width() // 2,
                geometry.bottom() - self.height() - 80,
            )

    # -- 事件 --------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None:
            self._drag_offset = None
            self._save_geometry()
            event.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_geometry()
        if self._tray is not None:
            self._tray.hide()
        super().closeEvent(event)
        QApplication.instance().quit()

    # -- 菜单 --------------------------------------------------------------
    def _show_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        toggle_french = QAction("显示法语原文", self)
        toggle_french.setCheckable(True)
        toggle_french.setChecked(self._show_french)
        toggle_french.triggered.connect(self._toggle_french)

        click_through = QAction("点击穿透", self)
        click_through.setCheckable(True)
        click_through.setChecked(self._click_through)
        click_through.triggered.connect(self._toggle_click_through)

        bigger = QAction("字号 +", self)
        bigger.triggered.connect(lambda: self._change_font(2))
        smaller = QAction("字号 -", self)
        smaller.triggered.connect(lambda: self._change_font(-2))

        hide = QAction("隐藏到托盘", self)
        hide.triggered.connect(self.hide)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(QApplication.instance().quit)

        menu.addAction(toggle_french)
        menu.addAction(click_through)
        menu.addSeparator()
        menu.addAction(bigger)
        menu.addAction(smaller)
        menu.addSeparator()
        menu.addAction(hide)
        menu.addAction(quit_action)
        menu.exec(self.mapToGlobal(position))

    def _toggle_french(self, checked: bool) -> None:
        self._show_french = checked
        self._french.setVisible(checked)
        self._save_geometry()

    def _toggle_click_through(self, checked: bool) -> None:
        self._click_through = checked
        self._apply_click_through(checked)
        self._save_geometry()

    def _apply_click_through(self, enabled: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, enabled)
        # 修改窗口标志后需要重新 show 才生效
        self.show()

    def _change_font(self, delta: int) -> None:
        self._font_size = max(12, min(48, self._font_size + delta))
        self._french.setFont(QFont("Microsoft YaHei UI", max(10, self._font_size - 8)))
        self._chinese.setFont(QFont("Microsoft YaHei UI", self._font_size, QFont.Weight.DemiBold))
        self._status.setFont(QFont("Microsoft YaHei UI", max(9, self._font_size - 11)))
        self._save_geometry()

    # -- 更新内容 ----------------------------------------------------------
    def on_status(self, message: str, level: str = "info") -> None:
        colour = "rgba(255,200,120,230)" if level == "warning" else "rgba(160,220,255,230)"
        if level == "error":
            colour = "rgba(255,140,140,240)"
        self._status.setStyleSheet(f"color: {colour};")
        self._status.setText(message)
        # 加载类提示保留，监听类提示几秒后淡出
        if "监听" in message or "加载完成" in message:
            self._status_timer.start(6000)
        else:
            self._status_timer.stop()

    def on_caption(self, french: str, chinese: str) -> None:
        self._french.setText(french)
        self._french.setVisible(self._show_french)
        self._chinese.setText(chinese or "…")
        self._status.setText("")

    def on_translation_failed(self, french: str, reason: str = "") -> None:
        self._french.setText(french)
        self._french.setVisible(self._show_french)
        self._chinese.setText("（翻译失败，已跳过本句）")
        self._status.setText(reason)
        self._status.setStyleSheet("color: rgba(255,180,120,230);")

    def _clear_status(self) -> None:
        self._status.setText("")

    def _save_geometry(self) -> None:
        geometry = {
            "x": self.x(),
            "y": self.y(),
            "width": self.width(),
            "font_size": self._font_size,
            "show_french": self._show_french,
            "click_through": self._click_through,
        }
        error = update_user_config(self._paths, {"gui": geometry})
        if error:
            logger.warning("%s", error)


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
class _SilentUI:
    """设备选择时不弹控制台，只记录日志。"""

    def status(self, message: str, level: str = "info") -> None:
        logger.info("%s", message)

    def ask(self, prompt: str) -> str:
        return ""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="fr-translator-gui", description="fr-translator 悬浮字幕")
    parser.add_argument("--version", action="version", version=f"fr-translator {__version__}")
    parser.add_argument("--config", action="append", default=[], metavar="FILE")
    parser.add_argument("--device", default=None, metavar="NAME_OR_ID")
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def _setup_logging(config: AppConfig, paths: AppPaths, debug: bool) -> None:
    log_file = config.logging.resolve_file(paths.root)
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_file = paths.writable_log_dir() / log_file.name
    logging.basicConfig(
        filename=str(log_file),
        level=logging.DEBUG if debug else getattr(logging, config.logging.level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        encoding="utf-8",
        force=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("fr-translator")

    try:
        paths = AppPaths.discover()
        config = load_config(paths, args.config)
        if args.device:
            config.audio.device = args.device
            config.audio.device_id = ""
        _setup_logging(config, paths, args.debug)
        logger.info("GUI 启动，根目录：%s", paths.root)

        devices = list_loopback_devices()
        device, changed = resolve_capture_device(
            devices,
            config.audio.device,
            config.audio.device_id,
            _SilentUI(),
            allow_prompt=False,
        )
        if changed:
            update_user_config(paths, {"audio": {"device": device.name, "device_id": device.id}})

        offline, notes = resolve_offline(
            paths,
            config,
            force_online=args.online,
            force_offline=args.offline,
        )
        for level, message in notes:
            logger.info("[%s] %s", level, message)
        edition = detect_edition(paths, config)

        overlay = Overlay(config, paths)
        bridge = Bridge()
        source = LoopbackCapture(
            device,
            samplerate=config.audio.samplerate,
            channels=config.audio.channels,
            block_frames=config.audio.block_frames,
        )
        engine = TranslationEngine(
            config,
            paths,
            source,
            _SinkAdapter(bridge),
            offline=offline,
            edition=edition,
        )
        worker = PipelineWorker(engine, bridge)

        bridge.status.connect(overlay.on_status)
        bridge.caption.connect(overlay.on_caption)
        bridge.translation_failed.connect(overlay.on_translation_failed)

        def on_fatal(message: str) -> None:
            overlay.on_status(message, "error")
            QMessageBox.critical(overlay, "fr-translator", message)
            app.quit()

        bridge.fatal.connect(on_fatal)

        overlay.show()
        overlay.on_status(f"正在监听：{device.display_name}", "info")
        worker.start()
        QTimer.singleShot(100, lambda: overlay.on_status("正在加载模型，请稍候…", "info"))
        code = app.exec()
        worker.stop()
        worker.wait(5000)
        return int(code)
    except FrTranslatorError as exc:
        logger.error("%s", exc)
        QMessageBox.critical(None, "fr-translator", str(exc))
        return 1
    except Exception as exc:  # pragma: no cover
        logger.exception("GUI 未预期错误")
        QMessageBox.critical(None, "fr-translator", f"程序遇到未预期的错误：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
