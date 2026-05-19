from __future__ import annotations

import fcntl
import os
import select
import struct
import threading
from dataclasses import dataclass
from pathlib import Path


class InputDeviceError(RuntimeError):
    pass


@dataclass
class InputDeviceInfo:
    path: str
    name: str


EV_KEY = 0x01
EV_REL = 0x02

KEY_VOLUMEDOWN = 114
KEY_VOLUMEUP = 115

REL_HWHEEL = 0x06
REL_DIAL = 0x07
REL_WHEEL = 0x08

EVIOCGRAB = 0x40044590
INPUT_EVENT_STRUCT = struct.Struct("llHHi")


def list_input_devices() -> list[InputDeviceInfo]:
    input_root = Path("/dev/input")
    sysfs_root = Path("/sys/class/input")
    seen_paths: set[str] = set()
    devices: list[InputDeviceInfo] = []

    if input_root.exists():
        for event_path in sorted(input_root.glob("event*")):
            name = event_path.name
            sysfs_name_path = sysfs_root / event_path.name / "device" / "name"
            if sysfs_name_path.exists():
                try:
                    loaded_name = sysfs_name_path.read_text(encoding="utf-8").strip()
                except OSError:
                    loaded_name = ""
                if loaded_name:
                    name = loaded_name
            device_path = str(event_path)
            devices.append(InputDeviceInfo(path=device_path, name=name))
            seen_paths.add(device_path)

    for device in _list_input_devices_from_procfs():
        if device.path in seen_paths:
            continue
        devices.append(device)
        seen_paths.add(device.path)
    return devices


def _list_input_devices_from_procfs() -> list[InputDeviceInfo]:
    procfs_path = Path("/proc/bus/input/devices")
    if not procfs_path.exists():
        return []

    try:
        content = procfs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    devices: list[InputDeviceInfo] = []
    for block in content.split("\n\n"):
        name = ""
        handlers: list[str] = []
        for line in block.splitlines():
            if line.startswith("N: Name="):
                name = line.split("=", 1)[1].strip().strip('"')
            if line.startswith("H: Handlers="):
                handlers = line.split("=", 1)[1].strip().split()
        for handler in handlers:
            if not handler.startswith("event"):
                continue
            devices.append(
                InputDeviceInfo(
                    path=f"/dev/input/{handler}",
                    name=name or handler,
                )
            )
    return devices


def _translate_event(event_type: int, event_code: int, event_value: int) -> int:
    if event_type == EV_KEY and event_value in (1, 2):
        if event_code == KEY_VOLUMEUP:
            return 1
        if event_code == KEY_VOLUMEDOWN:
            return -1
    if event_type == EV_REL and event_code in {REL_DIAL, REL_WHEEL, REL_HWHEEL}:
        return event_value
    return 0


class InputDeviceLearner:
    def __init__(self, on_detected, on_error, on_started=None) -> None:
        self.on_detected = on_detected
        self.on_error = on_error
        self.on_started = on_started
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._fds: dict[int, str] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._close_fds()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None

    def _run(self) -> None:
        devices = list_input_devices()
        if not devices:
            self.on_error("No /dev/input/event* devices found.")
            return

        opened = 0
        denied = 0
        for device in devices:
            try:
                fd = os.open(device.path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                denied += 1
                continue
            self._fds[fd] = device.path
            opened += 1

        if not opened:
            self.on_error("Unable to open any input event devices. Check /dev/input permissions.")
            return

        if self.on_started is not None:
            self.on_started(opened, denied)

        try:
            while not self._stop_event.is_set():
                try:
                    readable, _, _ = select.select(list(self._fds), [], [], 0.2)
                except (OSError, ValueError):
                    break
                if not readable:
                    continue
                for fd in readable:
                    path = self._fds.get(fd, "")
                    if not path:
                        continue
                    try:
                        payload = os.read(fd, INPUT_EVENT_STRUCT.size * 16)
                    except BlockingIOError:
                        continue
                    except OSError:
                        continue
                    if not payload:
                        continue
                    for offset in range(0, len(payload) - INPUT_EVENT_STRUCT.size + 1, INPUT_EVENT_STRUCT.size):
                        _seconds, _microseconds, event_type, event_code, event_value = INPUT_EVENT_STRUCT.unpack(
                            payload[offset : offset + INPUT_EVENT_STRUCT.size]
                        )
                        delta = _translate_event(event_type, event_code, event_value)
                        if delta:
                            self.on_detected(path)
                            return
        finally:
            self._close_fds()

    def _close_fds(self) -> None:
        for fd in list(self._fds):
            try:
                os.close(fd)
            except OSError:
                pass
            self._fds.pop(fd, None)


class VolumeKnobListener:
    def __init__(self, device_path: str, on_delta, exclusive: bool = False) -> None:
        self.device_path = device_path
        self.on_delta = on_delta
        self.exclusive = exclusive
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._fd: int | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        try:
            self._fd = os.open(self.device_path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            raise InputDeviceError(f"Unable to open input device {self.device_path}: {exc.strerror or exc}") from exc

        if self.exclusive:
            try:
                fcntl.ioctl(self._fd, EVIOCGRAB, 1)
            except OSError as exc:
                os.close(self._fd)
                self._fd = None
                raise InputDeviceError(
                    f"Unable to grab input device {self.device_path}: {exc.strerror or exc}"
                ) from exc

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        fd = self._fd
        self._fd = None
        if fd is not None:
            try:
                if self.exclusive:
                    fcntl.ioctl(fd, EVIOCGRAB, 0)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None

    def _run(self) -> None:
        fd = self._fd
        if fd is None:
            return
        while not self._stop_event.is_set():
            try:
                readable, _, _ = select.select([fd], [], [], 0.2)
            except (OSError, ValueError):
                break
            if not readable:
                continue
            try:
                payload = os.read(fd, INPUT_EVENT_STRUCT.size * 16)
            except BlockingIOError:
                continue
            except OSError:
                break
            if not payload:
                continue
            for offset in range(0, len(payload) - INPUT_EVENT_STRUCT.size + 1, INPUT_EVENT_STRUCT.size):
                _seconds, _microseconds, event_type, event_code, event_value = INPUT_EVENT_STRUCT.unpack(
                    payload[offset : offset + INPUT_EVENT_STRUCT.size]
                )
                delta = _translate_event(event_type, event_code, event_value)
                if delta:
                    self.on_delta(delta)
