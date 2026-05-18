from __future__ import annotations

import json
import math
import os
import shlex
import struct
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

from .models import RouteTargetSelection, RoutingMatrix, SinkInfo, SourceInfo, VirtualDeviceSpec


class AudioBackendError(RuntimeError):
    pass


class PactlBackend:
    VIRTUAL_DEVICES = (
        VirtualDeviceSpec("system_playback", "vm_system", "VM_System"),
        VirtualDeviceSpec("virtual_input_1", "vm_input_1", "VM_Input_1"),
        VirtualDeviceSpec("virtual_input_2", "vm_input_2", "VM_Input_2"),
        VirtualDeviceSpec("bus_b1", "vm_bus_b1", "VM_Bus_B1", "vm_out_b1", "VM_Output_B1"),
        VirtualDeviceSpec("bus_b2", "vm_bus_b2", "VM_Bus_B2", "vm_out_b2", "VM_Output_B2"),
    )

    TARGET_TO_BUS_KEY = {
        "B1": "bus_b1",
        "B2": "bus_b2",
    }

    def __init__(self, state_path: Path | None = None, config_path: Path | None = None) -> None:
        self.state_path = state_path or Path(".mixer-state.json")
        self.config_path = config_path or Path("mixer-config.json")

    def list_sinks(self) -> list[SinkInfo]:
        return self._parse_short_list("sinks", SinkInfo)

    def list_sources(self) -> list[SourceInfo]:
        return self._parse_short_list("sources", SourceInfo)

    def ensure_virtual_devices(self) -> list[str]:
        sinks = {sink.name for sink in self.list_sinks()}
        created: list[str] = []
        state = self._load_state()

        for spec in self.VIRTUAL_DEVICES:
            if spec.sink_name in sinks:
                continue
            module_id = self._load_null_sink(spec)
            state["virtual_modules"][spec.key] = module_id
            created.append(spec.description)

        sources = {source.name for source in self.list_sources()}
        for spec in self.VIRTUAL_DEVICES:
            if not spec.source_name:
                continue
            if spec.source_name in sources:
                continue
            module_id = self._load_remap_source(spec)
            state["virtual_source_modules"][spec.key] = module_id
            created.append(spec.source_description)

        self._save_state(state)
        return created

    def apply_routing(
        self,
        source_map: dict[str, str],
        matrix: RoutingMatrix,
        targets: RouteTargetSelection,
        strip_settings: dict[str, dict] | None = None,
    ) -> None:
        self.ensure_virtual_devices()
        state = self._load_state()
        self._unload_modules(state.get("normalize_modules", []))
        self._unload_modules(state.get("loopback_modules", []))

        normalized_source_map, normalize_modules = self._normalize_source_map(source_map)
        loopbacks: list[int] = []
        active_loopbacks: list[dict[str, int | str]] = []
        for source_key, target_key in matrix.enabled_pairs():
            source_name = normalized_source_map.get(source_key, "")
            sink_name = self._resolve_target_sink(target_key, targets)
            if not source_name or not sink_name:
                continue
            loopback_module_id = self._load_loopback(source_name, sink_name, source_key, target_key)
            loopbacks.append(loopback_module_id)
            active_loopbacks.append(
                {
                    "module_id": loopback_module_id,
                    "source_key": source_key,
                    "target_key": target_key,
                }
            )
            strip_volume = self._strip_volume_percent(strip_settings, source_key)
            sink_input_id = self._find_loopback_sink_input_id(loopback_module_id)
            if sink_input_id is not None:
                self._set_sink_input_volume(sink_input_id, strip_volume)

        state["normalize_modules"] = normalize_modules
        state["loopback_modules"] = loopbacks
        state["routing"] = {
            "active_loopbacks": active_loopbacks,
            "source_map": source_map,
            "targets": asdict(targets),
            "matrix": matrix.routes,
        }
        self._save_state(state)
        self.save_config(source_map, matrix, targets, strip_settings=strip_settings)

    def virtual_source_lookup(self) -> dict[str, str]:
        sources = self.list_sources()
        lookup: dict[str, str] = {}
        for spec in self.VIRTUAL_DEVICES:
            if spec.source_name:
                for source in sources:
                    if source.name == spec.source_name:
                        lookup[spec.key] = source.name
                        break
                if spec.key in lookup:
                    continue
            for source in sources:
                if not source.name.endswith(".monitor"):
                    continue
                if spec.sink_name in source.name:
                    lookup[spec.key] = source.name
                    break
        return lookup

    def test_output(self, sink_name: str, seconds: float = 2.0) -> None:
        if not sink_name:
            raise AudioBackendError("Select an output first.")
        pcm = self._build_test_tone(seconds=seconds)
        args = [
            "pw-play",
            "--raw",
            "--rate=48000",
            "--channels=2",
            "--format=s16",
            f"--target={sink_name}",
            "-",
        ]
        self._run_with_stdin(args, pcm)

    def test_input(self, source_name: str, sink_name: str, seconds: float = 4.0) -> None:
        if not source_name:
            raise AudioBackendError("Select an input first.")
        if not sink_name:
            raise AudioBackendError("Select an A1 output first for input testing.")
        normalized_source, normalize_module_id = self._ensure_stereo_source(source_name, "input_test")
        module_id = self._load_loopback(normalized_source, sink_name, "input_test", "preview")
        try:
            time.sleep(seconds)
        finally:
            try:
                self._run(["pactl", "unload-module", str(module_id)])
            except AudioBackendError:
                pass
            if normalize_module_id is not None:
                try:
                    self._run(["pactl", "unload-module", str(normalize_module_id)])
                except AudioBackendError:
                    pass

    def set_default_sink(self, sink_name: str) -> None:
        if not sink_name:
            raise AudioBackendError("No sink selected.")
        self._run(["pactl", "set-default-sink", sink_name])

    def save_config(
        self,
        source_map: dict[str, str],
        matrix: RoutingMatrix,
        targets: RouteTargetSelection,
        strip_settings: dict[str, dict] | None = None,
    ) -> None:
        existing_config = self.load_config()
        payload = {
            "targets": asdict(targets),
            "sources": {
                "hardware_input_1": source_map.get("hardware_input_1", ""),
                "hardware_input_2": source_map.get("hardware_input_2", ""),
            },
            "matrix": matrix.routes,
            "strip_settings": strip_settings if strip_settings is not None else existing_config.get("strip_settings", {}),
            "controller_binding": existing_config.get("controller_binding", {}),
            "selection_keybinds": existing_config.get("selection_keybinds", {}),
            "volume_keybinds": existing_config.get("volume_keybinds", {}),
        }
        self._write_config(payload)

    def load_config(self) -> dict:
        if not self.config_path.exists():
            return {}
        with self.config_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def update_strip_volume(self, source_key: str, volume_percent: int) -> int:
        volume = max(0, min(150, int(round(volume_percent))))
        state = self._load_state()
        active_loopbacks = state.get("routing", {}).get("active_loopbacks", [])
        for loopback in active_loopbacks:
            if loopback.get("source_key") != source_key:
                continue
            module_id = int(loopback.get("module_id", -1))
            if module_id < 0:
                continue
            sink_input_id = self._find_loopback_sink_input_id(module_id, attempts=4, delay_seconds=0.02)
            if sink_input_id is not None:
                self._set_sink_input_volume(sink_input_id, volume)

        config = self.load_config()
        strip_settings = config.setdefault("strip_settings", {})
        source_settings = strip_settings.setdefault(source_key, {})
        source_settings["volume_percent"] = volume
        self._write_config(config)
        return volume

    def save_controller_binding(self, binding: dict) -> None:
        config = self.load_config()
        config["controller_binding"] = binding
        self._write_config(config)

    def save_selection_keybinds(self, keybinds: dict[str, str]) -> None:
        config = self.load_config()
        config["selection_keybinds"] = keybinds
        self._write_config(config)

    def save_volume_keybinds(self, keybinds: dict[str, str | int]) -> None:
        config = self.load_config()
        config["volume_keybinds"] = keybinds
        self._write_config(config)

    def _strip_volume_percent(self, strip_settings: dict[str, dict] | None, source_key: str) -> int:
        if not strip_settings:
            return 100
        source_settings = strip_settings.get(source_key, {})
        value = source_settings.get("volume_percent", 100)
        try:
            volume = int(round(float(value)))
        except (TypeError, ValueError):
            return 100
        return max(0, min(150, volume))

    def _resolve_target_sink(self, target_key: str, targets: RouteTargetSelection) -> str:
        if target_key == "A1":
            return targets.a1_sink
        if target_key == "A2":
            return targets.a2_sink
        bus_key = self.TARGET_TO_BUS_KEY.get(target_key)
        if not bus_key:
            return ""
        for spec in self.VIRTUAL_DEVICES:
            if spec.key == bus_key:
                return spec.sink_name
        return ""

    def _parse_short_list(self, noun: str, model_cls: type[SinkInfo] | type[SourceInfo]):
        if noun == "sources":
            parsed_sources = self._parse_sources_json()
            if parsed_sources:
                return parsed_sources
        output = self._run(["pactl", "list", "short", noun])
        items = []
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            name = parts[1].strip()
            description = parts[1].strip()
            items.append(model_cls(name=name, description=description))
        return items

    def _parse_sources_json(self) -> list[SourceInfo]:
        try:
            output = self._run(["pactl", "-f", "json", "list", "sources"])
        except AudioBackendError:
            return []
        if not output:
            return []
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return []

        items: list[SourceInfo] = []
        for entry in payload:
            props = entry.get("properties", {})
            channel_map = entry.get("channel_map", "")
            if isinstance(channel_map, list):
                channel_map = ",".join(channel_map)
            items.append(
                SourceInfo(
                    name=entry.get("name", ""),
                    description=props.get("device.description", entry.get("name", "")),
                    channels=int(entry.get("sample_spec", {}).get("channels", 0) or 0),
                    channel_map=str(channel_map or ""),
                )
            )
        return items

    def _list_sink_inputs(self) -> list[dict]:
        try:
            output = self._run(["pactl", "-f", "json", "list", "sink-inputs"])
        except AudioBackendError:
            return []
        if not output:
            return []
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return []
        if isinstance(payload, list):
            return payload
        return []

    def _load_null_sink(self, spec: VirtualDeviceSpec) -> int:
        args = [
            "pactl",
            "load-module",
            "module-null-sink",
            f"sink_name={spec.sink_name}",
            "channels=2",
            "rate=48000",
            f"sink_properties=device.description={spec.description}",
        ]
        result = self._run(args)
        return int(result.strip())

    def _load_remap_source(self, spec: VirtualDeviceSpec) -> int:
        if not spec.source_name:
            raise AudioBackendError(f"Virtual device {spec.key} does not define a remap source.")
        args = [
            "pactl",
            "load-module",
            "module-remap-source",
            f"source_name={spec.source_name}",
            f"master={spec.sink_name}.monitor",
            "channels=2",
            "channel_map=front-left,front-right",
            "master_channel_map=front-left,front-right",
            "remix=yes",
            (
                "source_properties="
                f"device.description={spec.source_description},"
                f"node.description={spec.source_description}"
            ),
        ]
        result = self._run(args)
        return int(result.strip())

    def _load_stereo_remap_source(self, source_name: str, normalized_name: str, master_channel: str) -> int:
        args = [
            "pactl",
            "load-module",
            "module-remap-source",
            f"source_name={normalized_name}",
            f"master={source_name}",
            "channels=2",
            f"master_channel_map={master_channel},{master_channel}",
            "channel_map=front-left,front-right",
            "remix=no",
            (
                "source_properties="
                "device.description=Normalized Stereo Input,"
                "node.description=Normalized Stereo Input"
            ),
        ]
        result = self._run(args)
        return int(result.strip())

    def _load_loopback(self, source: str, sink: str, source_key: str, target_key: str) -> int:
        props = ",".join(
            [
                "application.name=AudioMixerMVP",
                f"node.description={source_key}_to_{target_key}",
            ]
        )
        args = [
            "pactl",
            "load-module",
            "module-loopback",
            f"source={source}",
            f"sink={sink}",
            "latency_msec=20",
            "channels=2",
            "channel_map=front-left,front-right",
            "master_channel_map=front-left,front-right",
            "remix=yes",
            f"sink_input_properties={props}",
        ]
        result = self._run(args)
        return int(result.strip())

    def _find_loopback_sink_input_id(self, module_id: int, attempts: int = 20, delay_seconds: float = 0.05) -> int | None:
        for _ in range(attempts):
            for sink_input in self._list_sink_inputs():
                if int(sink_input.get("owner_module", -1) or -1) != module_id:
                    continue
                sink_input_id = sink_input.get("index")
                if sink_input_id is None:
                    continue
                return int(sink_input_id)
            time.sleep(delay_seconds)
        return None

    def _set_sink_input_volume(self, sink_input_id: int, volume_percent: int) -> None:
        self._run(["pactl", "set-sink-input-volume", str(sink_input_id), f"{volume_percent}%"])

    def _unload_modules(self, module_ids: list[int]) -> None:
        for module_id in module_ids:
            try:
                self._run(["pactl", "unload-module", str(module_id)])
            except AudioBackendError:
                continue

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {
                "virtual_modules": {},
                "virtual_source_modules": {},
                "normalize_modules": [],
                "loopback_modules": [],
                "routing": {},
            }
        with self.state_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _save_state(self, state: dict) -> None:
        with self.state_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)

    def _write_config(self, payload: dict) -> None:
        with self.config_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)

    def _normalize_source_map(self, source_map: dict[str, str]) -> tuple[dict[str, str], list[int]]:
        normalized = dict(source_map)
        modules: list[int] = []
        for source_key in ("hardware_input_1", "hardware_input_2"):
            source_name = source_map.get(source_key, "")
            if not source_name:
                continue
            normalized_name, module_id = self._ensure_stereo_source(source_name, source_key)
            normalized[source_key] = normalized_name
            if module_id is not None:
                modules.append(module_id)
        return normalized, modules

    def _ensure_stereo_source(self, source_name: str, suffix: str) -> tuple[str, int | None]:
        info = self._get_source_info(source_name)
        if not info:
            return source_name, None
        master_channel = self._mono_master_channel(info)
        if not master_channel:
            return source_name, None
        normalized_name = f"amx_{suffix}_stereo"
        module_id = self._load_stereo_remap_source(source_name, normalized_name, master_channel)
        return normalized_name, module_id

    def _get_source_info(self, source_name: str) -> SourceInfo | None:
        for source in self.list_sources():
            if source.name == source_name:
                return source
        return None

    def _mono_master_channel(self, source_info: SourceInfo) -> str:
        channel_map = source_info.channel_map.strip()
        if source_info.channels == 1:
            if channel_map:
                return channel_map.split(",")[0]
            return "mono"
        if channel_map in {"mono", "front-center", "center"}:
            return channel_map
        return ""

    def _build_test_tone(self, seconds: float, sample_rate: int = 48000) -> bytes:
        frame_count = max(1, int(sample_rate * seconds))
        amplitude = 0.18
        frequency = 440.0
        pcm = bytearray()
        for index in range(frame_count):
            value = int(32767 * amplitude * math.sin((2.0 * math.pi * frequency * index) / sample_rate))
            if index < sample_rate // 6:
                value = int(value * (index / max(1, sample_rate // 6)))
            pcm.extend(struct.pack("<hh", value, value))
        return bytes(pcm)

    def _run_with_stdin(self, args: list[str], stdin_bytes: bytes) -> str:
        try:
            completed = subprocess.run(
                args,
                input=stdin_bytes,
                check=True,
                text=False,
                capture_output=True,
                env=os.environ.copy(),
            )
        except FileNotFoundError as exc:
            raise AudioBackendError(f"Missing required command: {args[0]}") from exc
        except subprocess.CalledProcessError as exc:
            command = " ".join(shlex.quote(part) for part in args)
            stderr = exc.stderr.decode("utf-8", errors="replace").strip() or exc.stdout.decode("utf-8", errors="replace").strip() or "Unknown error"
            raise AudioBackendError(f"{command} failed: {stderr}") from exc
        return completed.stdout.decode("utf-8", errors="replace").strip()

    def _run(self, args: list[str]) -> str:
        try:
            completed = subprocess.run(
                args,
                check=True,
                text=True,
                capture_output=True,
                env=os.environ.copy(),
            )
        except FileNotFoundError as exc:
            raise AudioBackendError(f"Missing required command: {args[0]}") from exc
        except subprocess.CalledProcessError as exc:
            command = " ".join(shlex.quote(part) for part in args)
            stderr = exc.stderr.strip() or exc.stdout.strip() or "Unknown error"
            raise AudioBackendError(f"{command} failed: {stderr}") from exc
        return completed.stdout.strip()
