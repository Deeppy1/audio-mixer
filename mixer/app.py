#Version 0.1.0 18/15/26
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
from pathlib import Path

from .audio_backend import AudioBackendError, PactlBackend
from .models import RouteTargetSelection, RoutingMatrix


CONTROL_SOCKET_PATH = Path("/tmp/audio-mixer-control.sock")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audio mixer MVP")
    parser.add_argument("--gui", action="store_true", help="Launch the Tk GUI")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("list", help="List available sinks, sources, and virtual monitor sources")
    subparsers.add_parser("create-devices", help="Create or repair the virtual sinks")

    apply_parser = subparsers.add_parser("apply", help="Apply routing from named sources to buses")
    apply_parser.add_argument("--a1", default="", help="Physical sink name for A1")
    apply_parser.add_argument("--a2", default="", help="Physical sink name for A2")
    apply_parser.add_argument("--hw1", default="", help="Physical source name for Hardware In 1")
    apply_parser.add_argument("--hw2", default="", help="Physical source name for Hardware In 2")
    apply_parser.add_argument(
        "--route",
        action="append",
        default=[],
        metavar="SOURCE:TARGET",
        help="Enable a route such as hw1:A1, hw1:B1, vi1:A2, vi2:B2",
    )

    output_test_parser = subparsers.add_parser("test-output", help="Play a short test tone to a sink")
    output_test_parser.add_argument("--sink", required=True, help="Sink name to test")
    output_test_parser.add_argument("--seconds", type=float, default=2.0, help="Test tone length in seconds")

    input_test_parser = subparsers.add_parser("test-input", help="Pass an input through to a sink for testing")
    input_test_parser.add_argument("--source", required=True, help="Source name to test")
    input_test_parser.add_argument("--sink", required=True, help="Sink name to hear the source on")
    input_test_parser.add_argument("--seconds", type=float, default=4.0, help="Pass-through length in seconds")
    subparsers.add_parser("apply-saved", help="Apply routing from mixer-config.json")
    subparsers.add_parser("set-default-system", help="Set VM_System as the default desktop playback sink")

    select_parser = subparsers.add_parser("select-strip", help="Select a strip in the running GUI")
    select_parser.add_argument("strip", help="Strip key or alias such as hw1, hw2, sys, vi1, vi2")

    volume_up_parser = subparsers.add_parser("volume-up", help="Raise the selected strip volume in the running GUI")
    volume_up_parser.add_argument("--steps", type=int, default=1, help="Number of configured volume steps")

    volume_down_parser = subparsers.add_parser("volume-down", help="Lower the selected strip volume in the running GUI")
    volume_down_parser.add_argument("--steps", type=int, default=1, help="Number of configured volume steps")
    return parser


def _source_aliases() -> dict[str, str]:
    return {
        "hw1": "hardware_input_1",
        "hw2": "hardware_input_2",
        "sys": "system_playback",
        "in1": "virtual_input_1",
        "in2": "virtual_input_2",
        "vi1": "virtual_input_1",
        "vi2": "virtual_input_2",
        "hardware_input_1": "hardware_input_1",
        "hardware_input_2": "hardware_input_2",
        "system_playback": "system_playback",
        "virtual_input_1": "virtual_input_1",
        "virtual_input_2": "virtual_input_2",
    }


def _resolve_source_key(value: str) -> str:
    return _source_aliases().get(value.lower().strip(), "")


def _print_devices(backend: PactlBackend) -> int:
    sinks = backend.list_sinks()
    sources = backend.list_sources()
    virtual_sources = backend.virtual_source_lookup()

    print("Sinks:")
    for sink in sinks:
        print(f"  {sink.name}")

    print("Sources:")
    for source in sources:
        print(f"  {source.name}")

    print("Virtual monitor sources:")
    for key in ("system_playback", "virtual_input_1", "virtual_input_2", "bus_b1", "bus_b2"):
        print(f"  {key}: {virtual_sources.get(key, 'missing')}")
    return 0


def _parse_routes(route_args: list[str]) -> RoutingMatrix:
    source_aliases = {
        "hw1": "hardware_input_1",
        "hw2": "hardware_input_2",
        "sys": "system_playback",
        "vi1": "virtual_input_1",
        "vi2": "virtual_input_2",
    }
    target_aliases = {"A1": "A1", "A2": "A2", "B1": "B1", "B2": "B2"}
    matrix_data = {
        "hardware_input_1": {"A1": False, "A2": False, "B1": False, "B2": False},
        "hardware_input_2": {"A1": False, "A2": False, "B1": False, "B2": False},
        "system_playback": {"A1": False, "A2": False, "B1": False, "B2": False},
        "virtual_input_1": {"A1": False, "A2": False, "B1": False, "B2": False},
        "virtual_input_2": {"A1": False, "A2": False, "B1": False, "B2": False},
    }

    for item in route_args:
        if ":" not in item:
            raise AudioBackendError(f"Invalid route '{item}'. Use SOURCE:TARGET.")
        source_alias, target_alias = item.split(":", 1)
        source_key = source_aliases.get(source_alias.lower())
        target_key = target_aliases.get(target_alias.upper())
        if not source_key or not target_key:
            raise AudioBackendError(f"Invalid route '{item}'.")
        matrix_data[source_key][target_key] = True
    return RoutingMatrix(routes=matrix_data)


def _apply_cli(backend: PactlBackend, args: argparse.Namespace) -> int:
    backend.ensure_virtual_devices()
    source_map = {
        "hardware_input_1": args.hw1,
        "hardware_input_2": args.hw2,
    }
    source_map.update(backend.virtual_source_lookup())
    source_map.setdefault("system_playback", "")
    source_map.setdefault("virtual_input_1", "")
    source_map.setdefault("virtual_input_2", "")

    targets = RouteTargetSelection(a1_sink=args.a1, a2_sink=args.a2)
    matrix = _parse_routes(args.route)
    backend.apply_routing(source_map, matrix, targets)
    print("Routing applied.")
    return 0


def _apply_saved_config(backend: PactlBackend) -> int:
    config = backend.load_config()
    if not config:
        raise AudioBackendError("No saved config found.")

    source_map = {
        "hardware_input_1": config.get("sources", {}).get("hardware_input_1", ""),
        "hardware_input_2": config.get("sources", {}).get("hardware_input_2", ""),
    }
    source_map.update(backend.virtual_source_lookup())
    source_map.setdefault("system_playback", "")
    source_map.setdefault("virtual_input_1", "")
    source_map.setdefault("virtual_input_2", "")

    targets = RouteTargetSelection(
        a1_sink=config.get("targets", {}).get("a1_sink", ""),
        a2_sink=config.get("targets", {}).get("a2_sink", ""),
    )
    matrix = RoutingMatrix(routes=config.get("matrix", {}))
    backend.apply_routing(
        source_map,
        matrix,
        targets,
        strip_settings=config.get("strip_settings", {}),
    )
    print("Saved routing applied.")
    return 0


def _test_output_cli(backend: PactlBackend, args: argparse.Namespace) -> int:
    backend.test_output(args.sink, seconds=args.seconds)
    print(f"Tested output {args.sink}.")
    return 0


def _test_input_cli(backend: PactlBackend, args: argparse.Namespace) -> int:
    backend.test_input(args.source, args.sink, seconds=args.seconds)
    print(f"Tested input {args.source} through {args.sink}.")
    return 0


def _set_default_system_cli(backend: PactlBackend) -> int:
    backend.ensure_virtual_devices()
    backend.set_default_sink("vm_system")
    print("Default sink set to VM_System.")
    return 0


def _send_remote_command(payload: dict) -> dict:
    if not CONTROL_SOCKET_PATH.exists():
        raise AudioBackendError("The mixer GUI control socket is not available. Start the GUI first.")

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(CONTROL_SOCKET_PATH))
            client.sendall(json.dumps(payload).encode("utf-8"))
            client.shutdown(socket.SHUT_WR)
            response = client.recv(8192)
    except OSError as exc:
        raise AudioBackendError(f"Unable to contact the running mixer GUI: {exc}") from exc

    if not response:
        raise AudioBackendError("The running mixer GUI returned an empty response.")

    try:
        decoded = json.loads(response.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise AudioBackendError("The running mixer GUI returned an invalid response.") from exc

    if not decoded.get("ok", False):
        raise AudioBackendError(decoded.get("error", "Unknown remote control error."))
    return decoded


def _select_strip_cli(args: argparse.Namespace) -> int:
    source_key = _resolve_source_key(args.strip)
    if not source_key:
        raise AudioBackendError(f"Unknown strip '{args.strip}'.")
    response = _send_remote_command({"action": "select-strip", "source_key": source_key})
    print(response.get("message", f"Selected {source_key}."))
    return 0


def _volume_adjust_cli(direction: str, args: argparse.Namespace) -> int:
    steps = max(1, int(args.steps))
    response = _send_remote_command({"action": direction, "steps": steps})
    print(response.get("message", "Volume updated."))
    return 0


def _run_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except ImportError as exc:
        raise AudioBackendError(
            "Tk is not available on this system. Install Tk/Tcl or run the CLI commands instead."
        ) from exc

    class MixerApp:
        ROUTE_TARGETS = ("A1", "A2", "B1", "B2")

        def __init__(self, root: tk.Tk) -> None:
            self.root = root
            self.backend = PactlBackend()
            self.root.title("Audio Mixer MVP")
            self.root.geometry("980x560")
            self.root.protocol("WM_DELETE_WINDOW", self.close)

            self.status_var = tk.StringVar(value="Ready")
            self.a1_var = tk.StringVar()
            self.a2_var = tk.StringVar()
            self.hw1_var = tk.StringVar()
            self.hw2_var = tk.StringVar()
            self.selected_strip_var = tk.StringVar()
            self.selected_strip_label_var = tk.StringVar()

            self.route_vars: dict[str, dict[str, tk.BooleanVar]] = {}
            self.route_rows = [
                ("hardware_input_1", "Hardware In 1"),
                ("hardware_input_2", "Hardware In 2"),
                ("system_playback", "System Playback"),
                ("virtual_input_1", "VM Input 1"),
                ("virtual_input_2", "VM Input 2"),
            ]
            self.strip_label_vars = {
                source_key: tk.StringVar(value=label)
                for source_key, label in self.route_rows
            }
            self.strip_mute_vars = {
                source_key: tk.BooleanVar(value=False)
                for source_key, _label in self.route_rows
            }
            self.strip_volume_vars = {
                source_key: tk.IntVar(value=100)
                for source_key, _label in self.route_rows
            }
            self.default_route_labels = dict(self.route_rows)
            self.selected_strip_var.set(self.route_rows[0][0])
            self.selected_strip_label_var.set(self.route_rows[0][1])
            self.selection_keybinds = {
                source_key: f"Ctrl+{index}"
                for index, (source_key, _label) in enumerate(self.route_rows, start=1)
            }
            self.volume_keybinds: dict[str, str | int] = {
                "up": "Ctrl+Up",
                "down": "Ctrl+Down",
                "step_percent": 5,
            }

            self.sinks: list[str] = []
            self.sources: list[str] = []
            self.pending_volume_jobs: dict[str, str] = {}
            self.loading_config = False
            self.keybind_window: tk.Toplevel | None = None
            self.keybind_value_vars: dict[str, tk.StringVar] = {}
            self.key_capture_target: tuple[str, str] | None = None
            self.command_server_socket: socket.socket | None = None
            self.command_server_thread: threading.Thread | None = None
            self.command_server_stop = threading.Event()

            self._build_ui()
            self._bind_shortcuts()
            self.refresh_devices()
            self._bind_volume_traces()
            self.loading_config = True
            self.load_saved_config()
            self.loading_config = False
            self._start_command_server()

        def _build_ui(self) -> None:
            top = ttk.Frame(self.root, padding=12)
            top.pack(fill="both", expand=True)

            control_bar = ttk.Frame(top)
            control_bar.pack(fill="x")

            ttk.Button(
                control_bar,
                text="Create / Repair Virtual Devices",
                command=self.create_virtual_devices,
            ).pack(side="left")
            ttk.Button(control_bar, text="Refresh", command=self.refresh_devices).pack(side="left", padx=8)
            ttk.Button(control_bar, text="Apply Routing", command=self.apply_routing).pack(side="left")
            ttk.Button(control_bar, text="Set Desktop To VM_System", command=self.set_default_system).pack(
                side="left", padx=8
            )
            ttk.Button(control_bar, text="Keybinds", command=self.open_keybind_window).pack(side="left")

            config_frame = ttk.LabelFrame(top, text="Bus Targets", padding=12)
            config_frame.pack(fill="x", pady=12)

            ttk.Label(config_frame, text="A1 Physical Output").grid(row=0, column=0, sticky="w")
            self.a1_combo = ttk.Combobox(config_frame, textvariable=self.a1_var, state="readonly")
            self.a1_combo.grid(row=0, column=1, sticky="ew", padx=(8, 24))
            ttk.Button(config_frame, text="Test A1", command=lambda: self.run_in_background(self.test_output_a1)).grid(
                row=0, column=2, padx=(8, 16)
            )

            ttk.Label(config_frame, text="A2 Physical Output").grid(row=0, column=3, sticky="w")
            self.a2_combo = ttk.Combobox(config_frame, textvariable=self.a2_var, state="readonly")
            self.a2_combo.grid(row=0, column=4, sticky="ew", padx=(8, 8))
            ttk.Button(config_frame, text="Test A2", command=lambda: self.run_in_background(self.test_output_a2)).grid(
                row=0, column=5
            )

            ttk.Label(config_frame, text="Hardware In 1").grid(row=1, column=0, sticky="w", pady=(10, 0))
            self.hw1_combo = ttk.Combobox(config_frame, textvariable=self.hw1_var, state="readonly")
            self.hw1_combo.grid(row=1, column=1, sticky="ew", padx=(8, 24), pady=(10, 0))
            ttk.Button(config_frame, text="Test In 1", command=lambda: self.run_in_background(self.test_input_1)).grid(
                row=1, column=2, padx=(8, 16), pady=(10, 0)
            )

            ttk.Label(config_frame, text="Hardware In 2").grid(row=1, column=3, sticky="w", pady=(10, 0))
            self.hw2_combo = ttk.Combobox(config_frame, textvariable=self.hw2_var, state="readonly")
            self.hw2_combo.grid(row=1, column=4, sticky="ew", padx=(8, 8), pady=(10, 0))
            ttk.Button(config_frame, text="Test In 2", command=lambda: self.run_in_background(self.test_input_2)).grid(
                row=1, column=5, pady=(10, 0)
            )

            config_frame.columnconfigure(1, weight=1)
            config_frame.columnconfigure(4, weight=1)

            matrix_frame = ttk.LabelFrame(top, text="Routing Matrix", padding=12)
            matrix_frame.pack(fill="both", expand=True)

            ttk.Label(matrix_frame, text="Source").grid(row=0, column=0, sticky="w", padx=(0, 16))
            ttk.Label(matrix_frame, text="Mute").grid(row=0, column=1, padx=12)
            ttk.Label(matrix_frame, text="Vol").grid(row=0, column=2, padx=12)
            for index, target in enumerate(self.ROUTE_TARGETS, start=1):
                ttk.Label(matrix_frame, text=target).grid(row=0, column=index + 2, padx=12)

            for row_index, (source_key, label) in enumerate(self.route_rows, start=1):
                label_entry = ttk.Entry(
                    matrix_frame,
                    textvariable=self.strip_label_vars[source_key],
                    width=22,
                )
                label_entry.grid(row=row_index, column=0, sticky="ew", padx=(0, 8))
                label_entry.bind("<Button-1>", lambda _event, source_key=source_key: self.select_strip(source_key))
                mute_box = ttk.Checkbutton(matrix_frame, variable=self.strip_mute_vars[source_key])
                mute_box.grid(
                    row=row_index,
                    column=1,
                )
                mute_box.bind("<Button-1>", lambda _event, source_key=source_key: self.select_strip(source_key))
                volume_scale = ttk.Scale(
                    matrix_frame,
                    from_=0,
                    to=150,
                    orient="horizontal",
                    variable=self.strip_volume_vars[source_key],
                )
                volume_scale.grid(row=row_index, column=2, sticky="ew", padx=(8, 8))
                volume_scale.bind("<Button-1>", lambda _event, source_key=source_key: self.select_strip(source_key))
                self.route_vars[source_key] = {}
                for col_index, target in enumerate(self.ROUTE_TARGETS, start=1):
                    variable = tk.BooleanVar(value=target in ("A1", "B1") and row_index == 1)
                    self.route_vars[source_key][target] = variable
                    route_box = ttk.Checkbutton(matrix_frame, variable=variable)
                    route_box.grid(row=row_index, column=col_index + 2)
                    route_box.bind("<Button-1>", lambda _event, source_key=source_key: self.select_strip(source_key))

            matrix_frame.columnconfigure(0, weight=1)
            matrix_frame.columnconfigure(2, weight=1)

            selected_strip_frame = ttk.Frame(top)
            selected_strip_frame.pack(fill="x", pady=(8, 0))
            ttk.Label(selected_strip_frame, text="Selected Fader").pack(side="left")
            ttk.Label(selected_strip_frame, textvariable=self.selected_strip_label_var).pack(side="left", padx=(8, 0))

            help_text = (
                "VM_System is a dedicated playback device for general desktop audio.\n"
                "VM_Input_1 and VM_Input_2 are extra playback devices for specific apps.\n"
                "VM_Bus_B1 and VM_Bus_B2 expose monitor sources that other apps can record.\n"
                "Click a strip to make it active, or configure selection shortcuts in Keybinds."
            )
            ttk.Label(top, text=help_text, justify="left").pack(anchor="w")

            status = ttk.Label(top, textvariable=self.status_var)
            status.pack(anchor="w", pady=(10, 0))

        def refresh_devices(self) -> None:
            try:
                sinks = self.backend.list_sinks()
                sources = self.backend.list_sources()
                virtual_sources = self.backend.virtual_source_lookup()
            except AudioBackendError as exc:
                self.status_var.set(str(exc))
                return

            self.sinks = [sink.name for sink in sinks if not sink.name.startswith("vm_")]
            self.sources = [source.name for source in sources if ".monitor" not in source.name]

            self.a1_combo["values"] = self.sinks
            self.a2_combo["values"] = self.sinks
            self.hw1_combo["values"] = self.sources
            self.hw2_combo["values"] = self.sources

            if self.sinks and not self.a1_var.get():
                self.a1_var.set(self.sinks[0])
            if len(self.sinks) > 1 and not self.a2_var.get():
                self.a2_var.set(self.sinks[1])
            elif self.sinks and not self.a2_var.get():
                self.a2_var.set(self.sinks[0])

            if self.sources and not self.hw1_var.get():
                self.hw1_var.set(self.sources[0])
            if len(self.sources) > 1 and not self.hw2_var.get():
                self.hw2_var.set(self.sources[1])

            b1_monitor = virtual_sources.get("bus_b1", "missing")
            b2_monitor = virtual_sources.get("bus_b2", "missing")
            self.status_var.set(
                f"Detected {len(self.sinks)} outputs, {len(self.sources)} inputs. "
                f"B1={b1_monitor} B2={b2_monitor}"
            )

        def _bind_shortcuts(self) -> None:
            self.root.bind_all("<KeyPress>", self._handle_keypress, add="+")

        def _handle_keypress(self, event) -> None:
            if self.key_capture_target is not None:
                return
            shortcut = self._event_to_shortcut(event)
            if not shortcut:
                return
            source_key = self._selection_source_for_shortcut(shortcut)
            if source_key:
                self.select_strip(source_key)
                return
            if shortcut == self.volume_keybinds.get("up", ""):
                self.adjust_selected_strip_volume(1)
                return
            if shortcut == self.volume_keybinds.get("down", ""):
                self.adjust_selected_strip_volume(-1)

        def select_strip(self, source_key: str) -> None:
            if source_key not in self.default_route_labels:
                return
            self.selected_strip_var.set(source_key)
            label = self.strip_label_vars[source_key].get().strip() or self.default_route_labels[source_key]
            self.selected_strip_label_var.set(label)
            self.status_var.set(f"Selected fader: {label}")

        def open_keybind_window(self) -> None:
            if self.keybind_window is not None and self.keybind_window.winfo_exists():
                self.keybind_window.deiconify()
                self.keybind_window.update_idletasks()
                self.keybind_window.lift()
                self.keybind_window.focus_set()
                self.status_var.set("Keybind window opened.")
                return

            window = tk.Toplevel(self.root)
            window.title("Keybinds")
            window.geometry("480x280")
            window.protocol("WM_DELETE_WINDOW", self.close_keybind_window)
            window.bind("<KeyPress>", self._capture_keybind)
            window.update_idletasks()
            window.deiconify()
            window.lift()
            window.focus_set()
            self.keybind_window = window
            self.keybind_value_vars = {}

            frame = ttk.Frame(window, padding=12)
            frame.pack(fill="both", expand=True)
            ttk.Label(frame, text="Select Fader Keybinds").grid(row=0, column=0, sticky="w")
            ttk.Label(frame, text="Shortcut").grid(row=0, column=1, sticky="w", padx=(12, 0))

            for row_index, (source_key, default_label) in enumerate(self.route_rows, start=1):
                label = self.strip_label_vars[source_key].get().strip() or default_label
                ttk.Label(frame, text=label).grid(row=row_index, column=0, sticky="w", pady=(8, 0))
                value_var = tk.StringVar(value=self.selection_keybinds.get(source_key, ""))
                self.keybind_value_vars[source_key] = value_var
                ttk.Label(frame, textvariable=value_var, width=18).grid(row=row_index, column=1, sticky="w", padx=(12, 12), pady=(8, 0))
                ttk.Button(
                    frame,
                    text="Set Key",
                    command=lambda source_key=source_key: self.begin_key_capture("select", source_key),
                ).grid(row=row_index, column=2, pady=(8, 0))
                ttk.Button(
                    frame,
                    text="Clear",
                    command=lambda source_key=source_key: self.clear_selection_keybind(source_key),
                ).grid(row=row_index, column=3, padx=(8, 0), pady=(8, 0))

            volume_row = len(self.route_rows) + 2
            ttk.Label(frame, text="Volume Controls").grid(row=volume_row, column=0, sticky="w", pady=(16, 0))

            up_row = volume_row + 1
            self.volume_up_var = tk.StringVar(value=str(self.volume_keybinds.get("up", "")))
            ttk.Label(frame, text="Selected Fader Up").grid(row=up_row, column=0, sticky="w", pady=(8, 0))
            ttk.Label(frame, textvariable=self.volume_up_var, width=18).grid(
                row=up_row, column=1, sticky="w", padx=(12, 12), pady=(8, 0)
            )
            ttk.Button(frame, text="Set Key", command=lambda: self.begin_key_capture("volume", "up")).grid(
                row=up_row, column=2, pady=(8, 0)
            )
            ttk.Button(frame, text="Clear", command=lambda: self.clear_volume_keybind("up")).grid(
                row=up_row, column=3, padx=(8, 0), pady=(8, 0)
            )

            down_row = volume_row + 2
            self.volume_down_var = tk.StringVar(value=str(self.volume_keybinds.get("down", "")))
            ttk.Label(frame, text="Selected Fader Down").grid(row=down_row, column=0, sticky="w", pady=(8, 0))
            ttk.Label(frame, textvariable=self.volume_down_var, width=18).grid(
                row=down_row, column=1, sticky="w", padx=(12, 12), pady=(8, 0)
            )
            ttk.Button(frame, text="Set Key", command=lambda: self.begin_key_capture("volume", "down")).grid(
                row=down_row, column=2, pady=(8, 0)
            )
            ttk.Button(frame, text="Clear", command=lambda: self.clear_volume_keybind("down")).grid(
                row=down_row, column=3, padx=(8, 0), pady=(8, 0)
            )

            step_row = volume_row + 3
            self.volume_step_var = tk.IntVar(value=int(self.volume_keybinds.get("step_percent", 5)))
            ttk.Label(frame, text="Step %").grid(row=step_row, column=0, sticky="w", pady=(8, 0))
            ttk.Spinbox(
                frame,
                from_=1,
                to=25,
                textvariable=self.volume_step_var,
                width=6,
                command=self.save_volume_keybind_settings,
            ).grid(row=step_row, column=1, sticky="w", padx=(12, 0), pady=(8, 0))
            self.volume_step_trace_id = self.volume_step_var.trace_add("write", lambda *_args: self.save_volume_keybind_settings())

            self.keybind_status_var = tk.StringVar(value="Click Set Key, then press the shortcut you want.")
            ttk.Label(frame, textvariable=self.keybind_status_var, justify="left").grid(
                row=step_row + 1,
                column=0,
                columnspan=4,
                sticky="w",
                pady=(16, 0),
            )
            self.status_var.set("Keybind window opened.")

        def close_keybind_window(self) -> None:
            self.key_capture_target = None
            if hasattr(self, "volume_step_var") and hasattr(self, "volume_step_trace_id"):
                try:
                    self.volume_step_var.trace_remove("write", self.volume_step_trace_id)
                except (AttributeError, tk.TclError):
                    pass
            if self.keybind_window is not None and self.keybind_window.winfo_exists():
                self.keybind_window.destroy()
            self.keybind_window = None

        def begin_key_capture(self, capture_type: str, target_key: str) -> None:
            self.key_capture_target = (capture_type, target_key)
            if self.keybind_window is not None:
                self.keybind_window.focus_force()
            if capture_type == "select":
                label = self.strip_label_vars[target_key].get().strip() or self.default_route_labels[target_key]
                self.keybind_status_var.set(f"Press the shortcut for {label}.")
            elif target_key == "up":
                self.keybind_status_var.set("Press the shortcut for selected fader volume up.")
            else:
                self.keybind_status_var.set("Press the shortcut for selected fader volume down.")

        def clear_selection_keybind(self, source_key: str) -> None:
            self.selection_keybinds[source_key] = ""
            if source_key in self.keybind_value_vars:
                self.keybind_value_vars[source_key].set("")
            self.backend.save_selection_keybinds(self.selection_keybinds)
            self.keybind_status_var.set("Shortcut cleared.")

        def clear_volume_keybind(self, direction: str) -> None:
            self.volume_keybinds[direction] = ""
            if direction == "up":
                self.volume_up_var.set("")
            else:
                self.volume_down_var.set("")
            self.save_volume_keybind_settings()
            self.keybind_status_var.set("Shortcut cleared.")

        def _capture_keybind(self, event) -> None:
            if self.key_capture_target is None:
                return
            shortcut = self._event_to_shortcut(event)
            if not shortcut:
                return

            capture_type, target_key = self.key_capture_target
            self._clear_shortcut_conflicts(shortcut, preserve=(capture_type, target_key))

            if capture_type == "select":
                self.selection_keybinds[target_key] = shortcut
                self.keybind_value_vars[target_key].set(shortcut)
                self.backend.save_selection_keybinds(self.selection_keybinds)
                label = self.strip_label_vars[target_key].get().strip() or self.default_route_labels[target_key]
                self.keybind_status_var.set(f"{label} is now bound to {shortcut}.")
            else:
                self.volume_keybinds[target_key] = shortcut
                if target_key == "up":
                    self.volume_up_var.set(shortcut)
                    self.keybind_status_var.set(f"Selected fader volume up is now bound to {shortcut}.")
                else:
                    self.volume_down_var.set(shortcut)
                    self.keybind_status_var.set(f"Selected fader volume down is now bound to {shortcut}.")
                self.save_volume_keybind_settings()
            self.key_capture_target = None

        def _event_to_shortcut(self, event) -> str:
            if event.keysym in {"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R"}:
                return ""
            parts: list[str] = []
            if event.state & 0x0004:
                parts.append("Ctrl")
            if event.state & 0x0001:
                parts.append("Shift")
            if event.state & 0x0008:
                parts.append("Alt")

            key_name = event.keysym
            if key_name.startswith("KP_"):
                key_name = key_name[3:]
            elif len(key_name) == 1:
                key_name = key_name.upper()

            return "+".join([*parts, key_name]) if parts or key_name else ""

        def _selection_source_for_shortcut(self, shortcut: str) -> str:
            for source_key, bound_shortcut in self.selection_keybinds.items():
                if bound_shortcut == shortcut:
                    return source_key
            return ""

        def _clear_shortcut_conflicts(self, shortcut: str, preserve: tuple[str, str]) -> None:
            for source_key, bound_shortcut in list(self.selection_keybinds.items()):
                if bound_shortcut != shortcut or preserve == ("select", source_key):
                    continue
                self.selection_keybinds[source_key] = ""
                if source_key in self.keybind_value_vars:
                    self.keybind_value_vars[source_key].set("")

            for direction in ("up", "down"):
                if self.volume_keybinds.get(direction, "") != shortcut or preserve == ("volume", direction):
                    continue
                self.volume_keybinds[direction] = ""
                if hasattr(self, "volume_up_var") and direction == "up":
                    self.volume_up_var.set("")
                if hasattr(self, "volume_down_var") and direction == "down":
                    self.volume_down_var.set("")

        def save_volume_keybind_settings(self) -> None:
            step_value = 5
            if hasattr(self, "volume_step_var"):
                try:
                    step_value = int(self.volume_step_var.get())
                except (TypeError, ValueError):
                    step_value = 5
            step_value = max(1, min(25, step_value))
            self.volume_keybinds["step_percent"] = step_value
            self.backend.save_volume_keybinds(self.volume_keybinds)

        def adjust_selected_strip_volume(self, direction: int) -> None:
            source_key = self.selected_strip_var.get()
            if source_key not in self.strip_volume_vars:
                return
            step_percent = int(self.volume_keybinds.get("step_percent", 5) or 5)
            current_volume = self.strip_volume_vars[source_key].get()
            new_volume = max(0, min(150, current_volume + (direction * step_percent)))
            if new_volume == current_volume:
                return
            self.strip_volume_vars[source_key].set(new_volume)
            label = self.strip_label_vars[source_key].get().strip() or self.default_route_labels[source_key]
            self.status_var.set(f"{label} volume {new_volume}%")

        def _start_command_server(self) -> None:
            self.command_server_stop.clear()
            self._cleanup_control_socket()
            try:
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                server.bind(str(CONTROL_SOCKET_PATH))
                server.listen()
                server.settimeout(0.5)
            except OSError as exc:
                self.status_var.set(f"Remote control disabled: {exc}")
                return

            self.command_server_socket = server
            self.command_server_thread = threading.Thread(target=self._command_server_loop, daemon=True)
            self.command_server_thread.start()

        def _command_server_loop(self) -> None:
            server = self.command_server_socket
            if server is None:
                return
            while not self.command_server_stop.is_set():
                try:
                    conn, _addr = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                with conn:
                    try:
                        raw = conn.recv(8192)
                        payload = json.loads(raw.decode("utf-8"))
                    except (OSError, json.JSONDecodeError):
                        self._send_command_response(conn, {"ok": False, "error": "Invalid command payload."})
                        continue

                    response_holder: dict[str, dict] = {}
                    done = threading.Event()
                    self.root.after(0, lambda payload=payload: self._execute_remote_command(payload, response_holder, done))
                    done.wait(timeout=2.0)
                    response = response_holder.get("response", {"ok": False, "error": "Command timed out."})
                    self._send_command_response(conn, response)

        def _execute_remote_command(self, payload: dict, response_holder: dict[str, dict], done: threading.Event) -> None:
            try:
                action = payload.get("action", "")
                if action == "select-strip":
                    source_key = str(payload.get("source_key", ""))
                    if source_key not in self.default_route_labels:
                        raise AudioBackendError(f"Unknown strip '{source_key}'.")
                    self.select_strip(source_key)
                    label = self.strip_label_vars[source_key].get().strip() or self.default_route_labels[source_key]
                    response_holder["response"] = {"ok": True, "message": f"Selected fader: {label}"}
                elif action in {"volume-up", "volume-down"}:
                    steps = max(1, int(payload.get("steps", 1)))
                    direction = 1 if action == "volume-up" else -1
                    for _ in range(steps):
                        self.adjust_selected_strip_volume(direction)
                    label = self.selected_strip_label_var.get()
                    volume = self.strip_volume_vars[self.selected_strip_var.get()].get()
                    response_holder["response"] = {"ok": True, "message": f"{label} volume {volume}%"}
                else:
                    raise AudioBackendError(f"Unknown remote action '{action}'.")
            except (AudioBackendError, TypeError, ValueError) as exc:
                response_holder["response"] = {"ok": False, "error": str(exc)}
            finally:
                done.set()

        def _send_command_response(self, conn, response: dict) -> None:
            try:
                conn.sendall(json.dumps(response).encode("utf-8"))
            except OSError:
                pass

        def _stop_command_server(self) -> None:
            self.command_server_stop.set()
            if self.command_server_socket is not None:
                try:
                    self.command_server_socket.close()
                except OSError:
                    pass
                self.command_server_socket = None
            self._cleanup_control_socket()

        def _cleanup_control_socket(self) -> None:
            try:
                if CONTROL_SOCKET_PATH.exists() or CONTROL_SOCKET_PATH.is_socket():
                    CONTROL_SOCKET_PATH.unlink()
            except OSError:
                pass

        def load_saved_config(self) -> None:
            config = self.backend.load_config()
            if not config:
                return

            sources = config.get("sources", {})
            targets = config.get("targets", {})
            matrix = config.get("matrix", {})
            strip_settings = config.get("strip_settings", {})
            saved_selection_keybinds = config.get("selection_keybinds", {})
            saved_volume_keybinds = config.get("volume_keybinds", {})

            if targets.get("a1_sink") in self.sinks:
                self.a1_var.set(targets["a1_sink"])
            if targets.get("a2_sink") in self.sinks:
                self.a2_var.set(targets["a2_sink"])
            if sources.get("hardware_input_1") in self.sources:
                self.hw1_var.set(sources["hardware_input_1"])
            if sources.get("hardware_input_2") in self.sources:
                self.hw2_var.set(sources["hardware_input_2"])

            for source_key, target_map in matrix.items():
                if source_key not in self.route_vars:
                    continue
                for target_key, enabled in target_map.items():
                    if target_key in self.route_vars[source_key]:
                        self.route_vars[source_key][target_key].set(bool(enabled))

            for source_key, settings in strip_settings.items():
                if source_key in self.strip_label_vars:
                    label = settings.get("label", "").strip()
                    if label:
                        self.strip_label_vars[source_key].set(label)
                if source_key in self.strip_mute_vars:
                    self.strip_mute_vars[source_key].set(bool(settings.get("muted", False)))
                if source_key in self.strip_volume_vars:
                    volume = settings.get("volume_percent", 100)
                    try:
                        self.strip_volume_vars[source_key].set(int(round(float(volume))))
                    except (TypeError, ValueError):
                        self.strip_volume_vars[source_key].set(100)

            if self.route_rows:
                self.select_strip(self.route_rows[0][0])
            for source_key in self.selection_keybinds:
                shortcut = saved_selection_keybinds.get(source_key)
                if isinstance(shortcut, str):
                    self.selection_keybinds[source_key] = shortcut
            for key in ("up", "down"):
                shortcut = saved_volume_keybinds.get(key)
                if isinstance(shortcut, str):
                    self.volume_keybinds[key] = shortcut
            try:
                self.volume_keybinds["step_percent"] = int(saved_volume_keybinds.get("step_percent", 5))
            except (TypeError, ValueError):
                self.volume_keybinds["step_percent"] = 5

        def create_virtual_devices(self) -> None:
            try:
                created = self.backend.ensure_virtual_devices()
                self.refresh_devices()
            except AudioBackendError as exc:
                messagebox.showerror("Audio backend error", str(exc))
                self.status_var.set(str(exc))
                return

            if created:
                self.status_var.set(f"Created: {', '.join(created)}")
            else:
                self.status_var.set("Virtual devices already present")

        def set_default_system(self) -> None:
            try:
                self.backend.ensure_virtual_devices()
                self.backend.set_default_sink("vm_system")
            except AudioBackendError as exc:
                messagebox.showerror("Audio backend error", str(exc))
                self.status_var.set(str(exc))
                return
            self.status_var.set("Default desktop sink set to VM_System")

        def apply_routing(self) -> None:
            source_map = {
                "hardware_input_1": self.hw1_var.get(),
                "hardware_input_2": self.hw2_var.get(),
            }
            source_map.update(self.backend.virtual_source_lookup())

            for key in ("system_playback", "virtual_input_1", "virtual_input_2"):
                source_map.setdefault(key, "")

            matrix_data = {}
            for source_key, targets in self.route_vars.items():
                muted = self.strip_mute_vars[source_key].get()
                matrix_data[source_key] = {
                    target_key: (False if muted else variable.get())
                    for target_key, variable in targets.items()
                }
            matrix = RoutingMatrix(routes=matrix_data)
            targets = RouteTargetSelection(a1_sink=self.a1_var.get(), a2_sink=self.a2_var.get())
            strip_settings = {
                source_key: {
                    "label": self.strip_label_vars[source_key].get().strip() or default_label,
                    "muted": self.strip_mute_vars[source_key].get(),
                    "volume_percent": self.strip_volume_vars[source_key].get(),
                }
                for source_key, default_label in self.route_rows
            }

            try:
                self.backend.apply_routing(source_map, matrix, targets, strip_settings=strip_settings)
            except AudioBackendError as exc:
                messagebox.showerror("Audio backend error", str(exc))
                self.status_var.set(str(exc))
                return

            self.status_var.set("Routing applied")

        def _bind_volume_traces(self) -> None:
            for source_key, variable in self.strip_volume_vars.items():
                variable.trace_add("write", lambda *_args, source_key=source_key: self._queue_strip_volume_update(source_key))

        def _queue_strip_volume_update(self, source_key: str) -> None:
            if self.loading_config:
                return
            pending = self.pending_volume_jobs.pop(source_key, None)
            if pending:
                self.root.after_cancel(pending)
            self.pending_volume_jobs[source_key] = self.root.after(
                120,
                lambda source_key=source_key: self._apply_live_strip_volume(source_key),
            )

        def _apply_live_strip_volume(self, source_key: str) -> None:
            self.pending_volume_jobs.pop(source_key, None)
            if self.loading_config:
                return
            try:
                volume = self.backend.update_strip_volume(source_key, self.strip_volume_vars[source_key].get())
            except AudioBackendError as exc:
                self._show_backend_error(str(exc))
                return
            self.strip_volume_vars[source_key].set(volume)

        def run_in_background(self, action) -> None:
            thread = threading.Thread(target=action, daemon=True)
            thread.start()

        def _show_backend_error(self, message: str) -> None:
            self.root.after(
                0,
                lambda: (
                    messagebox.showerror("Audio backend error", message),
                    self.status_var.set(message),
                ),
            )

        def _set_status(self, message: str) -> None:
            self.root.after(0, lambda: self.status_var.set(message))

        def test_output_a1(self) -> None:
            try:
                sink_name = self.a1_var.get()
                self._set_status(f"Testing output {sink_name}...")
                self.backend.test_output(sink_name)
                self._set_status(f"Output test finished for {sink_name}")
            except AudioBackendError as exc:
                self._show_backend_error(str(exc))

        def test_output_a2(self) -> None:
            try:
                sink_name = self.a2_var.get()
                self._set_status(f"Testing output {sink_name}...")
                self.backend.test_output(sink_name)
                self._set_status(f"Output test finished for {sink_name}")
            except AudioBackendError as exc:
                self._show_backend_error(str(exc))

        def test_input_1(self) -> None:
            try:
                source_name = self.hw1_var.get()
                sink_name = self.a1_var.get()
                self._set_status(f"Testing input {source_name} through {sink_name}...")
                self.backend.test_input(source_name, sink_name)
                self._set_status(f"Input test finished for {source_name}")
            except AudioBackendError as exc:
                self._show_backend_error(str(exc))

        def test_input_2(self) -> None:
            try:
                source_name = self.hw2_var.get()
                sink_name = self.a1_var.get()
                self._set_status(f"Testing input {source_name} through {sink_name}...")
                self.backend.test_input(source_name, sink_name)
                self._set_status(f"Input test finished for {source_name}")
            except AudioBackendError as exc:
                self._show_backend_error(str(exc))

        def close(self) -> None:
            self.close_keybind_window()
            self._stop_command_server()
            self.root.destroy()

    root = tk.Tk()
    ttk.Style().theme_use("clam")
    MixerApp(root)
    root.mainloop()
    return 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    backend = PactlBackend()

    try:
        if args.gui:
            raise SystemExit(_run_gui())
        if args.command == "list":
            raise SystemExit(_print_devices(backend))
        if args.command == "create-devices":
            created = backend.ensure_virtual_devices()
            if created:
                print("Created:", ", ".join(created))
            else:
                print("Virtual devices already present.")
            raise SystemExit(0)
        if args.command == "apply":
            raise SystemExit(_apply_cli(backend, args))
        if args.command == "apply-saved":
            raise SystemExit(_apply_saved_config(backend))
        if args.command == "set-default-system":
            raise SystemExit(_set_default_system_cli(backend))
        if args.command == "test-output":
            raise SystemExit(_test_output_cli(backend, args))
        if args.command == "test-input":
            raise SystemExit(_test_input_cli(backend, args))
        if args.command == "select-strip":
            raise SystemExit(_select_strip_cli(args))
        if args.command == "volume-up":
            raise SystemExit(_volume_adjust_cli("volume-up", args))
        if args.command == "volume-down":
            raise SystemExit(_volume_adjust_cli("volume-down", args))

        parser.print_help()
        raise SystemExit(0)
    except AudioBackendError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
