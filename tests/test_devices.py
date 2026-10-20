from __future__ import annotations

import unittest

from fr_translator.devices import CaptureDevice, match_device, resolve_capture_device
from fr_translator.errors import DeviceError


class FakeUI:
    def __init__(self, answers: list[str] | None = None) -> None:
        self.answers = list(answers or [])
        self.messages: list[tuple[str, str]] = []

    def status(self, message: str, level: str = "info") -> None:
        self.messages.append((level, message))

    def ask(self, prompt: str) -> str:
        return self.answers.pop(0) if self.answers else ""


DEVICES = [
    CaptureDevice(id="id-speaker", name="扬声器 (Realtek)", speaker="扬声器 (Realtek)"),
    CaptureDevice(id="id-headset", name="耳机 (USB Audio)", speaker="耳机 (USB Audio)"),
]


class DeviceTests(unittest.TestCase):
    def test_match_by_id(self) -> None:
        self.assertEqual(match_device(DEVICES, "", "ID-HEADSET"), DEVICES[1])

    def test_match_by_name(self) -> None:
        self.assertEqual(match_device(DEVICES, "扬声器 (Realtek)").id, "id-speaker")

    def test_match_by_unique_substring(self) -> None:
        self.assertEqual(match_device(DEVICES, "耳机").id, "id-headset")

    def test_no_match(self) -> None:
        self.assertIsNone(match_device(DEVICES, "不存在的设备"))

    def test_single_device_is_selected_automatically(self) -> None:
        device, changed = resolve_capture_device([DEVICES[0]], "auto", "", FakeUI())
        self.assertEqual(device.id, "id-speaker")
        self.assertTrue(changed)

    def test_configured_device_is_not_changed(self) -> None:
        device, changed = resolve_capture_device(
            DEVICES, "耳机 (USB Audio)", "id-headset", FakeUI()
        )
        self.assertEqual(device.id, "id-headset")
        self.assertFalse(changed)

    def test_multiple_devices_prompt(self) -> None:
        ui = FakeUI(["2"])
        device, changed = resolve_capture_device(DEVICES, "auto", "", ui)
        self.assertEqual(device.id, "id-headset")
        self.assertTrue(changed)
        self.assertTrue(any("1." in message for _, message in ui.messages))

    def test_invalid_input_falls_back_to_first(self) -> None:
        device, _ = resolve_capture_device(DEVICES, "auto", "", FakeUI(["", ""]))
        self.assertEqual(device.id, "id-speaker")

    def test_missing_loopback_raises_chinese_error(self) -> None:
        with self.assertRaises(DeviceError) as ctx:
            resolve_capture_device([], "auto", "", FakeUI())
        self.assertIn("loopback", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
