"""Windows loopback（系统播放声音）设备枚举与选择。

只在需要时才 import soundcard，避免 ``--help`` / 配置校验阶段因为缺少
音频依赖而直接失败。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .errors import DependencyError, DeviceError


@dataclass(frozen=True)
class CaptureDevice:
    """一个可用于录制系统声音的 loopback 设备。"""

    id: str
    name: str
    speaker: str = ""

    @property
    def display_name(self) -> str:
        if self.speaker and self.speaker.casefold() != self.name.casefold():
            return f"{self.name}（{self.speaker}）"
        return self.name


def _import_soundcard() -> Any:
    try:
        import soundcard  # type: ignore import-not-found
    except ImportError as exc:  # pragma: no cover - 依赖缺失时才触发
        raise DependencyError(
            "缺少依赖 soundcard，无法捕获系统音频。\n"
            "源码运行时请执行：pip install -r requirements.txt\n"
            "便携包请确认 python\\Lib\\site-packages 下存在 soundcard。"
        ) from exc
    return soundcard


def list_loopback_devices() -> list[CaptureDevice]:
    """枚举所有 loopback 设备（每个正在使用的播放设备对应一个）。"""

    sc = _import_soundcard()
    try:
        speakers = {speaker.id: speaker.name for speaker in sc.all_speakers()}
        microphones = sc.all_microphones(include_loopback=True)
    except Exception as exc:  # soundcard 在无音频服务时会抛 COM 异常
        raise DeviceError(
            "无法枚举音频设备。\n"
            "请确认 Windows 音频服务（Audiosrv）正在运行，并且至少有一个可用的播放设备。\n"
            f"详细信息：{exc}"
        ) from exc

    result: list[CaptureDevice] = []
    seen: set[str] = set()
    for mic in microphones:
        is_loopback = bool(getattr(mic, "isloopback", False))
        if not is_loopback and mic.id not in speakers:
            continue
        if mic.id in seen:
            continue
        seen.add(mic.id)
        result.append(
            CaptureDevice(
                id=str(mic.id),
                name=str(mic.name),
                speaker=str(speakers.get(mic.id, "")),
            )
        )
    return result


def list_speaker_names() -> list[str]:
    sc = _import_soundcard()
    try:
        return [str(speaker.name) for speaker in sc.all_speakers()]
    except Exception:
        return []


def no_loopback_message() -> str:
    speakers = list_speaker_names()
    if speakers:
        speaker_lines = "\n".join(f"  - {name}" for name in speakers)
    else:
        speaker_lines = "  （未检测到任何播放设备）"
    return (
        "没有找到可以录制“系统播放声音”的回环设备（loopback）。\n\n"
        f"检测到的播放设备：\n{speaker_lines}\n\n"
        "请依次检查：\n"
        "  1. Windows 设置 → 系统 → 声音，确认“输出”设备存在且未被禁用；\n"
        "  2. 耳机/音箱是否插好，蓝牙耳机是否已连接；\n"
        "  3. 先播放一点声音，再启动本程序；\n"
        "  4. 远程桌面 / 虚拟机环境可能需要开启音频重定向。"
    )


def match_device(
    devices: Sequence[CaptureDevice],
    name: str,
    device_id: str = "",
) -> CaptureDevice | None:
    """按 id、完整名称、显示名、唯一子串的顺序匹配设备。"""

    wanted_id = (device_id or "").strip()
    if wanted_id:
        for device in devices:
            if device.id.casefold() == wanted_id.casefold():
                return device

    wanted = (name or "").strip()
    if not wanted or wanted.casefold() == "auto":
        return None

    for device in devices:
        if device.name.casefold() == wanted.casefold():
            return device
        if device.display_name.casefold() == wanted.casefold():
            return device

    partial = [d for d in devices if wanted.casefold() in d.name.casefold()]
    if len(partial) == 1:
        return partial[0]
    return None


def resolve_capture_device(
    devices: Sequence[CaptureDevice],
    configured_name: str,
    configured_id: str,
    ui: Any,
    *,
    allow_prompt: bool = True,
) -> tuple[CaptureDevice, bool]:
    """返回 (设备, 是否需要写回用户配置)。

    * 配置命中：直接使用；
    * 只有一个设备：自动选择；
    * 多个设备：列出编号让用户选择（非交互场景默认第一个）。
    """

    if not devices:
        raise DeviceError(no_loopback_message())

    matched = match_device(devices, configured_name, configured_id)
    if matched is not None:
        return matched, False

    if (configured_name or "").strip() and configured_name.strip().casefold() != "auto":
        ui.status(
            f"配置中的输出设备“{configured_name}”当前不可用，请重新选择。",
            "warning",
        )

    if len(devices) == 1:
        return devices[0], True

    if not allow_prompt:
        ui.status("检测到多个输出设备，已自动选择第一个。可用 --device 指定。", "warning")
        return devices[0], True

    ui.status("检测到多个可用的系统音频输出设备：")
    for index, device in enumerate(devices, start=1):
        ui.status(f"  {index}. {device.display_name}")
    while True:
        answer = ui.ask(f"请选择要监听的设备编号（1-{len(devices)}，直接回车选择 1）：").strip()
        if not answer:
            return devices[0], True
        if answer.isdigit() and 1 <= int(answer) <= len(devices):
            return devices[int(answer) - 1], True
        ui.status("输入无效，请输入列表中的编号。", "warning")
