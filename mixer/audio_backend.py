from __future__ import annotations

import json
import math
import os
import shlex
import signal
import struct
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

from .models import AppStreamInfo, RouteTargetSelection, RoutingMatrix, SinkInfo, SourceInfo, VirtualDeviceSpec


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
    APP_ASSIGNABLE_SOURCE_KEYS = ("system_playback", "virtual_input_1", "virtual_input_2")

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
        state.setdefault("virtual_modules", {})
        state.setdefault("virtual_source_modules", {})
        state.setdefault("normalize_modules", [])
        state.setdefault("loopback_modules", [])
        state.setdefault("routing", {})

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
        eq_settings: dict[str, dict] | None = None,
    ) -> None:
        self.ensure_virtual_devices()
        state = self._load_state()
        self._unload_modules(state.get("normalize_modules", []))
        self._unload_modules(state.get("loopback_modules", []))
        self._stop_eq_bridge_processes(state.get("eq_bridge_processes", []))

        normalized_source_map, normalize_modules = self._normalize_source_map(source_map)
        loopbacks: list[int] = []
        eq_bridge_processes: list[dict[str, int | str]] = []
        active_routes: list[dict[str, int | str]] = []
        eq_settings = eq_settings or self.load_config().get("eq_settings", {})
        for source_key, target_key in matrix.enabled_pairs():
            source_name = normalized_source_map.get(source_key, "")
            sink_name = self._resolve_target_sink(target_key, targets)
            if not source_name or not sink_name:
                continue
            strip_volume = self._strip_volume_percent(strip_settings, source_key)
            eq_state = eq_settings.get(source_key, {})
            if self._eq_enabled(eq_state):
                bridge_process = self._start_eq_bridge(source_name, sink_name, source_key, target_key, eq_state)
                eq_bridge_processes.append(bridge_process)
                active_routes.append(bridge_process)
                sink_input_id = self._find_sink_input_by_properties(
                    application_name=str(bridge_process["application_name"]),
                    media_name=str(bridge_process["stream_name"]),
                )
                if sink_input_id is not None:
                    self._set_sink_input_volume(sink_input_id, strip_volume)
                continue

            loopback_module_id = self._load_loopback(source_name, sink_name, source_key, target_key)
            loopbacks.append(loopback_module_id)
            route = {
                "type": "loopback",
                "module_id": loopback_module_id,
                "source_key": source_key,
                "target_key": target_key,
            }
            active_routes.append(route)
            sink_input_id = self._find_loopback_sink_input_id(loopback_module_id)
            if sink_input_id is not None:
                self._set_sink_input_volume(sink_input_id, strip_volume)

        state["normalize_modules"] = normalize_modules
        state["loopback_modules"] = loopbacks
        state["eq_bridge_processes"] = eq_bridge_processes
        state["routing"] = {
            "active_routes": active_routes,
            "active_loopbacks": [route for route in active_routes if route.get("type") == "loopback"],
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
            "ducking": existing_config.get("ducking", {}),
            "app_assignments": existing_config.get("app_assignments", {}),
            "eq_settings": existing_config.get("eq_settings", {}),
        }
        self._write_config(payload)

    def load_config(self) -> dict:
        if not self.config_path.exists():
            return {}
        with self.config_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def update_strip_volume(self, source_key: str, volume_percent: int) -> int:
        return self._set_strip_volume(source_key, volume_percent, persist=True)

    def apply_live_strip_volume(self, source_key: str, volume_percent: int) -> int:
        return self._set_strip_volume(source_key, volume_percent, persist=False)

    def _set_strip_volume(self, source_key: str, volume_percent: int, persist: bool) -> int:
        volume = max(0, min(150, int(round(volume_percent))))
        state = self._load_state()
        active_routes = state.get("routing", {}).get("active_routes", [])
        if not isinstance(active_routes, list) or not active_routes:
            active_routes = state.get("routing", {}).get("active_loopbacks", [])
        for route in active_routes:
            if route.get("source_key") != source_key:
                continue
            sink_input_id = None
            if route.get("type") == "eq_bridge":
                sink_input_id = self._find_sink_input_by_properties(
                    application_name=str(route.get("application_name", "")),
                    media_name=str(route.get("stream_name", "")),
                    attempts=4,
                    delay_seconds=0.02,
                )
            else:
                module_id = int(route.get("module_id", -1))
                if module_id >= 0:
                    sink_input_id = self._find_loopback_sink_input_id(module_id, attempts=4, delay_seconds=0.02)
            if sink_input_id is not None:
                self._set_sink_input_volume(sink_input_id, volume)
        if persist:
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

    def save_ducking_config(self, ducking: dict) -> None:
        config = self.load_config()
        config["ducking"] = ducking
        self._write_config(config)

    def save_app_assignments(self, assignments: dict[str, str]) -> None:
        config = self.load_config()
        config["app_assignments"] = assignments
        self._write_config(config)

    def save_eq_settings(self, eq_settings: dict[str, dict]) -> None:
        config = self.load_config()
        config["eq_settings"] = eq_settings
        self._write_config(config)

    def list_app_streams(self) -> list[AppStreamInfo]:
        streams: list[AppStreamInfo] = []
        sink_lookup = {sink.index: sink.name for sink in self.list_sinks() if sink.index >= 0}
        for entry in self._list_sink_inputs():
            props = entry.get("properties", {})
            if props.get("application.name") == "AudioMixerMVP":
                continue

            stream_id = entry.get("index")
            if stream_id is None:
                continue

            app_name = str(
                props.get("application.name")
                or props.get("application.process.binary")
                or props.get("media.name")
                or f"Stream {stream_id}"
            )
            app_id = str(
                props.get("application.process.binary")
                or props.get("application.name")
                or props.get("media.name")
                or f"stream-{stream_id}"
            ).strip()
            if not app_id:
                app_id = f"stream-{stream_id}"

            stream_name = str(props.get("media.name") or props.get("node.name") or app_name)
            sink_name = self._sink_name_from_entry(entry, sink_lookup)
            streams.append(
                AppStreamInfo(
                    stream_id=int(stream_id),
                    app_id=app_id,
                    app_name=app_name,
                    stream_name=stream_name,
                    sink_name=sink_name,
                )
            )
        streams.sort(key=lambda stream: (stream.app_name.lower(), stream.stream_name.lower(), stream.stream_id))
        return streams

    def move_app_stream_to_sink(self, stream_id: int, sink_name: str) -> None:
        self.ensure_virtual_devices()
        if not sink_name:
            raise AudioBackendError("Unknown app assignment target.")
        self._run(["pactl", "move-sink-input", str(stream_id), sink_name])

    def apply_app_assignments(self, assignments: dict[str, str] | None = None) -> int:
        if assignments is None:
            assignments = self.load_config().get("app_assignments", {})
        if not assignments:
            return 0

        moved = 0
        for stream in self.list_app_streams():
            target_value = assignments.get(stream.app_id, "")
            target_sink = self._normalize_app_assignment_target(target_value)
            if not target_sink:
                continue
            if stream.sink_name == target_sink:
                continue
            self._run(["pactl", "move-sink-input", str(stream.stream_id), target_sink])
            moved += 1
        return moved

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

    def _sink_name_for_app_source(self, source_key: str) -> str:
        for spec in self.VIRTUAL_DEVICES:
            if spec.key == source_key:
                return spec.sink_name
        return ""

    def _normalize_app_assignment_target(self, value: str) -> str:
        if not value:
            return ""
        if value in self.APP_ASSIGNABLE_SOURCE_KEYS:
            return self._sink_name_for_app_source(value)
        for sink in self.list_sinks():
            if sink.name == value:
                return sink.name
        return ""

    def _parse_short_list(self, noun: str, model_cls: type[SinkInfo] | type[SourceInfo]):
        if noun == "sinks":
            parsed_sinks = self._parse_sinks_json()
            if parsed_sinks:
                return parsed_sinks
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
            if model_cls is SinkInfo:
                try:
                    index = int(parts[0].strip())
                except ValueError:
                    index = -1
                items.append(model_cls(name=name, description=description, index=index))
            else:
                items.append(model_cls(name=name, description=description))
        return items

    def _parse_sinks_json(self) -> list[SinkInfo]:
        try:
            output = self._run(["pactl", "-f", "json", "list", "sinks"])
        except AudioBackendError:
            return []
        if not output:
            return []
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return []

        items: list[SinkInfo] = []
        for entry in payload:
            props = entry.get("properties", {})
            description = str(
                props.get("device.description")
                or props.get("node.description")
                or entry.get("description")
                or entry.get("name", "")
            )
            items.append(
                SinkInfo(
                    name=entry.get("name", ""),
                    description=description,
                    index=int(entry.get("index", -1) or -1),
                )
            )
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

    def _sink_name_from_entry(self, entry: dict, sink_lookup: dict[int, str] | None = None) -> str:
        sink = entry.get("sink", "")
        if isinstance(sink, dict):
            name = sink.get("name")
            if isinstance(name, str):
                return name
            index = sink.get("index")
            if sink_lookup and isinstance(index, int):
                return sink_lookup.get(index, "")
        if isinstance(sink, str):
            return sink
        if isinstance(sink, int) and sink_lookup:
            return sink_lookup.get(sink, "")
        sink_name = entry.get("sink_name")
        if isinstance(sink_name, str):
            return sink_name
        return ""

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

    def _find_sink_input_by_properties(
        self,
        application_name: str,
        media_name: str,
        attempts: int = 20,
        delay_seconds: float = 0.05,
    ) -> int | None:
        for _ in range(attempts):
            for sink_input in self._list_sink_inputs():
                props = sink_input.get("properties", {})
                if props.get("application.name") != application_name:
                    continue
                if media_name and props.get("media.name") != media_name:
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
        default_state = {
            "virtual_modules": {},
            "virtual_source_modules": {},
            "normalize_modules": [],
            "loopback_modules": [],
            "eq_bridge_processes": [],
            "routing": {},
        }
        if not self.state_path.exists():
            return default_state
        with self.state_path.open("r", encoding="utf-8") as handle:
            loaded_state = json.load(handle)

        if not isinstance(loaded_state, dict):
            return default_state

        state = dict(default_state)
        state.update(loaded_state)
        for key in ("virtual_modules", "virtual_source_modules", "routing"):
            if not isinstance(state.get(key), dict):
                state[key] = dict(default_state[key])
        for key in ("normalize_modules", "loopback_modules", "eq_bridge_processes"):
            if not isinstance(state.get(key), list):
                state[key] = list(default_state[key])
        return state

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

    def _eq_enabled(self, eq_state: dict) -> bool:
        if not isinstance(eq_state, dict):
            return False
        if bool(eq_state.get("bypass", False)):
            return False
        bands = eq_state.get("bands", [])
        if not isinstance(bands, list):
            return False
        for band in bands:
            if not isinstance(band, dict):
                continue
            if not bool(band.get("enabled", True)):
                continue
            try:
                gain_db = float(band.get("gain_db", 0.0))
            except (TypeError, ValueError):
                continue
            if abs(gain_db) >= 0.05:
                return True
        return False

    def _start_eq_bridge(
        self,
        source_name: str,
        sink_name: str,
        source_key: str,
        target_key: str,
        eq_state: dict,
    ) -> dict[str, int | str]:
        application_name = "AudioMixerEQ"
        stream_name = f"{source_key}_to_{target_key}_eq"
        filter_chain = self._build_ffmpeg_eq_filter(eq_state)
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-thread_queue_size",
            "1024",
            "-f",
            "pulse",
            "-name",
            application_name,
            "-stream_name",
            f"{source_key}_capture_eq",
            "-sample_rate",
            "48000",
            "-channels",
            "2",
            "-fragment_size",
            "4096",
            "-i",
            source_name,
            "-af",
            filter_chain,
            "-ac",
            "2",
            "-ar",
            "48000",
            "-f",
            "pulse",
            "-name",
            application_name,
            "-stream_name",
            stream_name,
            "-buffer_duration",
            "20",
            "-device",
            sink_name,
            sink_name,
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=os.environ.copy(),
            start_new_session=True,
        )
        return {
            "type": "eq_bridge",
            "pid": process.pid,
            "source_key": source_key,
            "target_key": target_key,
            "source_name": source_name,
            "sink_name": sink_name,
            "application_name": application_name,
            "stream_name": stream_name,
        }

    def _build_ffmpeg_eq_filter(self, eq_state: dict) -> str:
        bands = eq_state.get("bands", []) if isinstance(eq_state, dict) else []
        if not isinstance(bands, list) or not bands:
            return "anull"

        filters: list[str] = []
        for index, band in enumerate(bands):
            if not isinstance(band, dict) or not bool(band.get("enabled", True)):
                continue
            try:
                frequency = max(20.0, min(20000.0, float(band.get("frequency", 1000.0))))
                q = max(0.2, min(8.0, float(band.get("q", 1.0))))
                gain_db = max(-18.0, min(18.0, float(band.get("gain_db", 0.0))))
            except (TypeError, ValueError):
                continue
            if abs(gain_db) < 0.05:
                continue

            kind = str(band.get("kind", "peak"))
            if index == 0 or kind == "lowshelf":
                filters.append(f"bass=f={frequency}:t=q:w={q}:g={gain_db}")
            elif index == len(bands) - 1 or kind == "highshelf":
                filters.append(f"treble=f={frequency}:t=q:w={q}:g={gain_db}")
            else:
                filters.append(f"equalizer=f={frequency}:t=q:w={q}:g={gain_db}")
        return ",".join(filters) if filters else "anull"

    def _stop_eq_bridge_processes(self, processes: list[dict]) -> None:
        for process_info in processes:
            try:
                pid = int(process_info.get("pid", -1))
            except (TypeError, ValueError, AttributeError):
                continue
            if pid <= 0:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            except OSError:
                continue

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
