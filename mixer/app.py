#Version 0.1.2 19/15/26
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from version import version
from updatecheck import *
from .audio_backend import AudioBackendError, PactlBackend
from .models import RouteTargetSelection, RoutingMatrix


CONTROL_SOCKET_PATH = Path("/tmp/audio-mixer-control.sock")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audio mixer")
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
    from .ui_theme import PALETTE, apply_theme
    from .ui_widgets import AnimatedLevelMeter, NeonToggle

    class MixerApp:
        ROUTE_TARGETS = ("A1", "A2", "B1", "B2")
        DUCKED_SOURCE_KEYS = ("system_playback", "virtual_input_1", "virtual_input_2")
        APP_ASSIGNMENT_LABELS = {
            "system_playback": "System Playback",
            "virtual_input_1": "Input 1",
            "virtual_input_2": "Input 2",
        }

        @dataclass
        class MeterWorker:
            stop: threading.Event
            thread: threading.Thread
            source_name: str
            channels: int

        def __init__(self, root: tk.Tk) -> None:
            self.root = root
            self.backend = PactlBackend()
            self.palette = PALETTE
            self.style = apply_theme(self.root)
            self.root.title("Audio Mixer Studio")
            self.root.geometry("1460x880")
            self.root.minsize(1180, 760)
            self.root.protocol("WM_DELETE_WINDOW", self.close)

            self.status_var = tk.StringVar(value="Ready")
            self.a1_var = tk.StringVar()
            self.a2_var = tk.StringVar()
            self.hw1_var = tk.StringVar()
            self.hw2_var = tk.StringVar()
            self.selected_strip_var = tk.StringVar()
            self.selected_strip_label_var = tk.StringVar()
            self.selected_strip_hint_var = tk.StringVar()
            self.selected_strip_volume_var = tk.StringVar(value="100%")
            self.selected_strip_routes_var = tk.StringVar(value="No routes active")
            self.ducking_enabled_var = tk.BooleanVar(value=False)
            self.ducking_source_var = tk.StringVar(value="Hardware In 1")
            self.ducking_amount_var = tk.IntVar(value=35)
            self.ducking_threshold_var = tk.IntVar(value=10)

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
            self.strip_solo_vars = {
                source_key: tk.BooleanVar(value=False)
                for source_key, _label in self.route_rows
            }
            self.strip_volume_vars = {
                source_key: tk.IntVar(value=100)
                for source_key, _label in self.route_rows
            }
            self.strip_volume_readout_vars = {
                source_key: tk.StringVar(value="100%")
                for source_key, _label in self.route_rows
            }
            self.strip_subtitle_vars = {
                source_key: tk.StringVar(value="")
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
            self.virtual_sources: dict[str, str] = {}
            self.pending_volume_jobs: dict[str, str] = {}
            self.loading_config = False
            self.keybind_window: tk.Toplevel | None = None
            self.ducking_window: tk.Toplevel | None = None
            self.apps_window: tk.Toplevel | None = None
            self.apps_content_frame = None
            self.app_assignments: dict[str, str] = {}
            self.keybind_value_vars: dict[str, tk.StringVar] = {}
            self.key_capture_target: tuple[str, str] | None = None
            self.strip_card_frames: dict[str, ttk.Frame] = {}
            self.strip_card_bodies: dict[str, tk.Widget] = {}
            self.strip_meters: dict[str, AnimatedLevelMeter] = {}
            self.meter_levels = {source_key: 0.0 for source_key, _label in self.route_rows}
            self.meter_workers: dict[str, MixerApp.MeterWorker] = {}
            self.strip_layout_columns = 0
            self.layout_after_id: str | None = None
            self.command_server_socket: socket.socket | None = None
            self.command_server_thread: threading.Thread | None = None
            self.command_server_stop = threading.Event()
            self.ducking_thread: threading.Thread | None = None
            self.ducking_stop = threading.Event()
            self.ducking_active = False

            self._build_ui()
            self._bind_shortcuts()
            self.refresh_devices()
            self._bind_volume_traces()
            self.hw1_var.trace_add("write", lambda *_args: self._handle_meter_source_change())
            self.hw2_var.trace_add("write", lambda *_args: self._handle_meter_source_change())
            self.ducking_amount_var.trace_add("write", lambda *_args: self._update_ducking_config())
            self.ducking_threshold_var.trace_add("write", lambda *_args: self._update_ducking_config())
            self.loading_config = True
            self.load_saved_config()
            self.loading_config = False
            self._start_command_server()
            self._restart_meter_workers()

        def _build_ui(self) -> None:
            shell = ttk.Frame(self.root, style="Shell.TFrame", padding=20)
            shell.pack(fill="both", expand=True)
            shell.columnconfigure(1, weight=1)
            shell.rowconfigure(1, weight=1)

            header = ttk.Frame(shell, style="Toolbar.TFrame")
            header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 18))
            header.columnconfigure(0, weight=1)

            title_block = ttk.Frame(header, style="Toolbar.TFrame")
            title_block.grid(row=0, column=0, sticky="w")
            ttk.Label(title_block, text="Audio Mixer Studio", style="Title.TLabel").pack(anchor="w")
            ttk.Label(
                title_block,
                text="PipeWire routing control with persistent buses, strip selection, ducking, and app assignment.",
                style="Subtitle.TLabel",
            ).pack(anchor="w", pady=(4, 0))

            action_bar = ttk.Frame(header, style="Toolbar.TFrame")
            action_bar.grid(row=0, column=1, sticky="e")
            ttk.Button(
                action_bar,
                text="Create / Repair Virtual Devices",
                command=self.create_virtual_devices,
                style="Ghost.TButton",
            ).pack(side="left", padx=(0, 10))
            ttk.Button(action_bar, text="Refresh", command=self.refresh_devices, style="Ghost.TButton").pack(
                side="left", padx=(0, 10)
            )
            ttk.Button(action_bar, text="Apply Routing", command=self.apply_routing, style="Accent.TButton").pack(
                side="left", padx=(0, 10)
            )
            ttk.Button(
                action_bar,
                text="Set Desktop To VM_System",
                command=self.set_default_system,
                style="Ghost.TButton",
            ).pack(side="left", padx=(0, 10))
            ttk.Button(action_bar, text="Apps", command=self.open_apps_window).pack(side="left", padx=(0, 10))
            ttk.Button(action_bar, text="Keybinds", command=self.open_keybind_window).pack(side="left", padx=(0, 10))
            ttk.Button(action_bar, text="Ducking", command=self.open_ducking_window).pack(side="left")

            sidebar = ttk.Frame(shell, style="Panel.TFrame", padding=18)
            sidebar.grid(row=1, column=0, sticky="nsew", padx=(0, 18))
            sidebar.configure(width=320)
            sidebar.grid_propagate(False)
            sidebar.columnconfigure(0, weight=1)

            ttk.Label(sidebar, text="Bus Targets", style="Section.TLabel").grid(row=0, column=0, sticky="w")
            outputs_panel = ttk.LabelFrame(sidebar, text="Outputs", style="Panel.TLabelframe", padding=14)
            outputs_panel.grid(row=1, column=0, sticky="ew", pady=(12, 14))
            outputs_panel.columnconfigure(0, weight=1)

            ttk.Label(outputs_panel, text="A1 Physical Output", style="Body.TLabel").grid(row=0, column=0, sticky="w")
            self.a1_combo = ttk.Combobox(outputs_panel, textvariable=self.a1_var, state="readonly")
            self.a1_combo.grid(row=1, column=0, sticky="ew", pady=(6, 8))
            ttk.Button(
                outputs_panel,
                text="Test A1",
                command=lambda: self.run_in_background(self.test_output_a1),
                style="Ghost.TButton",
            ).grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(6, 8))

            ttk.Label(outputs_panel, text="A2 Physical Output", style="Body.TLabel").grid(row=2, column=0, sticky="w")
            self.a2_combo = ttk.Combobox(outputs_panel, textvariable=self.a2_var, state="readonly")
            self.a2_combo.grid(row=3, column=0, sticky="ew", pady=(6, 0))
            ttk.Button(
                outputs_panel,
                text="Test A2",
                command=lambda: self.run_in_background(self.test_output_a2),
                style="Ghost.TButton",
            ).grid(row=3, column=1, sticky="ew", padx=(10, 0), pady=(6, 0))

            inputs_panel = ttk.LabelFrame(sidebar, text="Hardware Inputs", style="Panel.TLabelframe", padding=14)
            inputs_panel.grid(row=2, column=0, sticky="ew", pady=(0, 14))
            inputs_panel.columnconfigure(0, weight=1)

            ttk.Label(inputs_panel, text="Hardware In 1", style="Body.TLabel").grid(row=0, column=0, sticky="w")
            self.hw1_combo = ttk.Combobox(inputs_panel, textvariable=self.hw1_var, state="readonly")
            self.hw1_combo.grid(row=1, column=0, sticky="ew", pady=(6, 8))
            ttk.Button(
                inputs_panel,
                text="Preview In 1",
                command=lambda: self.run_in_background(self.test_input_1),
                style="Ghost.TButton",
            ).grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(6, 8))

            ttk.Label(inputs_panel, text="Hardware In 2", style="Body.TLabel").grid(row=2, column=0, sticky="w")
            self.hw2_combo = ttk.Combobox(inputs_panel, textvariable=self.hw2_var, state="readonly")
            self.hw2_combo.grid(row=3, column=0, sticky="ew", pady=(6, 0))
            ttk.Button(
                inputs_panel,
                text="Preview In 2",
                command=lambda: self.run_in_background(self.test_input_2),
                style="Ghost.TButton",
            ).grid(row=3, column=1, sticky="ew", padx=(10, 0), pady=(6, 0))

            monitor_panel = ttk.LabelFrame(sidebar, text="Monitoring", style="Panel.TLabelframe", padding=14)
            monitor_panel.grid(row=3, column=0, sticky="ew", pady=(0, 14))
            monitor_panel.columnconfigure(0, weight=1)
            ttk.Label(monitor_panel, text="Selected Strip", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(monitor_panel, textvariable=self.selected_strip_label_var, style="Section.TLabel").grid(
                row=1, column=0, sticky="w", pady=(4, 0)
            )
            ttk.Label(monitor_panel, textvariable=self.selected_strip_hint_var, style="Muted.TLabel", wraplength=250).grid(
                row=2, column=0, sticky="w", pady=(4, 10)
            )
            self.monitor_meter = AnimatedLevelMeter(
                monitor_panel,
                level_getter=lambda: self._meter_level_for_strip(self.selected_strip_var.get()),
                orientation="horizontal",
                height=18,
                width=260,
                segments=24,
            )
            self.monitor_meter.grid(row=3, column=0, sticky="ew")
            ttk.Label(monitor_panel, text="Level", style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=(8, 0))
            ttk.Label(monitor_panel, textvariable=self.selected_strip_volume_var, style="Body.TLabel").grid(
                row=5, column=0, sticky="w"
            )
            ttk.Label(monitor_panel, text="Routes", style="Muted.TLabel").grid(row=6, column=0, sticky="w", pady=(8, 0))
            ttk.Label(monitor_panel, textvariable=self.selected_strip_routes_var, style="Body.TLabel", wraplength=250).grid(
                row=7, column=0, sticky="w"
            )

            status_panel = ttk.LabelFrame(sidebar, text="Session", style="Panel.TLabelframe", padding=14)
            status_panel.grid(row=4, column=0, sticky="nsew")
            status_panel.columnconfigure(0, weight=1)

            status = updatecheck()
            help_text = (
                "VM_System handles desktop playback.\n"
                "VM_Input_1 and VM_Input_2 are assignable app playback lanes.\n"
                "B1 and B2 expose monitor buses for capture apps.\n"
                f"Version: {version}  {status}"
            )
            ttk.Label(status_panel, text=help_text, style="Muted.TLabel", justify="left", wraplength=260).grid(
                row=0, column=0, sticky="w"
            )

            mixer_shell = ttk.Frame(shell, style="Surface.TFrame")
            mixer_shell.grid(row=1, column=1, sticky="nsew")
            mixer_shell.columnconfigure(0, weight=1)
            mixer_shell.rowconfigure(1, weight=1)

            mix_header = ttk.Frame(mixer_shell, style="Surface.TFrame")
            mix_header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
            mix_header.columnconfigure(0, weight=1)
            ttk.Label(mix_header, text="Routing Matrix", style="Section.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(
                mix_header,
                text="Editable strip labels, mute/solo, responsive channel cards, and illuminated A/B bus routing.",
                style="Subtitle.TLabel",
            ).grid(row=1, column=0, sticky="w", pady=(4, 0))

            self.strip_grid_frame = ttk.Frame(mixer_shell, style="Surface.TFrame")
            self.strip_grid_frame.grid(row=1, column=0, sticky="nsew")
            self.strip_grid_frame.bind("<Configure>", lambda _event: self._queue_strip_layout())

            for row_index, (source_key, label) in enumerate(self.route_rows, start=1):
                self._create_strip_card(source_key, label, row_index)

            self.status_bar = ttk.Frame(shell, style="Status.TFrame", padding=(16, 12))
            self.status_bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(18, 0))
            self.status_bar.columnconfigure(0, weight=1)
            ttk.Label(self.status_bar, textvariable=self.status_var, style="Status.TLabel").grid(
                row=0, column=0, sticky="w"
            )
            ttk.Label(self.status_bar, text="Remote control socket active", style="Badge.TLabel").grid(
                row=0, column=1, sticky="e"
            )

            self._refresh_strip_metadata()
            self._queue_strip_layout()

        def _create_strip_card(self, source_key: str, label: str, row_index: int) -> None:
            card = ttk.Frame(self.strip_grid_frame, style="Card.TFrame", padding=14)
            self.strip_card_frames[source_key] = card
            card.bind("<Button-1>", lambda _event, source_key=source_key: self.select_strip(source_key))

            header = ttk.Frame(card, style="Card.TFrame")
            header.pack(fill="x")
            header.bind("<Button-1>", lambda _event, source_key=source_key: self.select_strip(source_key))

            label_entry = ttk.Entry(header, textvariable=self.strip_label_vars[source_key], width=18)
            label_entry.pack(fill="x")
            label_entry.bind("<Button-1>", lambda _event, source_key=source_key: self.select_strip(source_key))

            subtitle = ttk.Label(header, textvariable=self.strip_subtitle_vars[source_key], style="CardSubtitle.TLabel")
            subtitle.pack(anchor="w", pady=(6, 0))
            subtitle.bind("<Button-1>", lambda _event, source_key=source_key: self.select_strip(source_key))

            channel_body = tk.Frame(card, bg=self.palette.panel_alt)
            channel_body.pack(fill="both", expand=True, pady=(14, 10))
            self.strip_card_bodies[source_key] = channel_body
            channel_body.bind("<Button-1>", lambda _event, source_key=source_key: self.select_strip(source_key))

            meter = AnimatedLevelMeter(
                channel_body,
                level_getter=lambda source_key=source_key: self._meter_level_for_strip(source_key),
                orientation="vertical",
                height=190,
                width=28,
                segments=28,
            )
            meter.pack(side="left", fill="y")
            meter.bind("<Button-1>", lambda _event, source_key=source_key: self.select_strip(source_key))
            self.strip_meters[source_key] = meter

            slider_column = tk.Frame(channel_body, bg=self.palette.panel_alt)
            slider_column.pack(side="left", fill="y", expand=True, padx=(14, 0))
            slider_column.bind("<Button-1>", lambda _event, source_key=source_key: self.select_strip(source_key))

            ttk.Label(slider_column, textvariable=self.strip_volume_readout_vars[source_key], style="CardTitle.TLabel").pack(
                anchor="center"
            )
            volume_scale = ttk.Scale(
                slider_column,
                from_=150,
                to=0,
                orient="vertical",
                variable=self.strip_volume_vars[source_key],
                style="Mixer.Vertical.TScale",
                length=210,
            )
            volume_scale.pack(fill="y", expand=True, pady=(10, 8))
            volume_scale.bind("<Button-1>", lambda _event, source_key=source_key: self.select_strip(source_key))
            ttk.Label(slider_column, text="Gain", style="CardSubtitle.TLabel").pack(anchor="center")

            controls = tk.Frame(card, bg=self.palette.panel_alt)
            controls.pack(fill="x", pady=(0, 10))

            mute_toggle = NeonToggle(
                controls,
                text="Mute",
                variable=self.strip_mute_vars[source_key],
                palette=self.palette,
                on_color=self.palette.danger,
                width=6,
                command=lambda source_key=source_key: self.select_strip(source_key),
            )
            mute_toggle.pack(side="left")
            solo_toggle = NeonToggle(
                controls,
                text="Solo",
                variable=self.strip_solo_vars[source_key],
                palette=self.palette,
                on_color=self.palette.warning,
                width=6,
                command=lambda source_key=source_key: self.select_strip(source_key),
            )
            solo_toggle.pack(side="left", padx=(8, 0))

            routes = tk.Frame(card, bg=self.palette.panel_alt)
            routes.pack(fill="x")
            self.route_vars[source_key] = {}
            route_colors = {
                "A1": self.palette.accent,
                "A2": self.palette.accent_bright,
                "B1": self.palette.accent_soft,
                "B2": "#C45EFF",
            }
            for target in self.ROUTE_TARGETS:
                variable = tk.BooleanVar(value=target in ("A1", "B1") and row_index == 1)
                self.route_vars[source_key][target] = variable
                toggle = NeonToggle(
                    routes,
                    text=target,
                    variable=variable,
                    palette=self.palette,
                    on_color=route_colors[target],
                    width=4,
                    command=lambda source_key=source_key: self.select_strip(source_key),
                )
                toggle.pack(side="left", padx=(0, 6))

        def _queue_strip_layout(self) -> None:
            if self.layout_after_id:
                self.root.after_cancel(self.layout_after_id)
            self.layout_after_id = self.root.after(40, self._layout_strip_cards)

        def _layout_strip_cards(self) -> None:
            self.layout_after_id = None
            width = max(1, self.strip_grid_frame.winfo_width())
            column_count = max(1, min(len(self.route_rows), width // 245))
            if column_count == self.strip_layout_columns and any(frame.winfo_manager() for frame in self.strip_card_frames.values()):
                return
            self.strip_layout_columns = column_count

            for child in self.strip_grid_frame.winfo_children():
                child.grid_forget()
            for index, (source_key, _label) in enumerate(self.route_rows):
                row = index // column_count
                column = index % column_count
                self.strip_card_frames[source_key].grid(row=row, column=column, sticky="nsew", padx=8, pady=8)
            for column in range(column_count):
                self.strip_grid_frame.columnconfigure(column, weight=1, uniform="strip")

        def _refresh_strip_metadata(self) -> None:
            for source_key, _label in self.route_rows:
                self.strip_subtitle_vars[source_key].set(self._strip_subtitle(source_key))
                self.strip_volume_readout_vars[source_key].set(f"{int(self.strip_volume_vars[source_key].get())}%")
            self._update_selected_strip_summary()

        def _strip_subtitle(self, source_key: str) -> str:
            if source_key == "hardware_input_1":
                return self._short_device_name(self.hw1_var.get()) or "Select physical input"
            if source_key == "hardware_input_2":
                return self._short_device_name(self.hw2_var.get()) or "Select physical input"
            if source_key == "system_playback":
                return "VM_System desktop playback lane"
            if source_key == "virtual_input_1":
                return "VM_Input_1 app playback lane"
            if source_key == "virtual_input_2":
                return "VM_Input_2 app playback lane"
            return ""

        def _short_device_name(self, value: str) -> str:
            if not value:
                return ""
            parts = [part for part in value.replace(".", "_").split("_") if part]
            if len(parts) <= 4:
                return value
            return " ".join(parts[-4:])

        def _handle_meter_source_change(self) -> None:
            self._refresh_strip_metadata()
            self._restart_meter_workers()

        def _meter_level_for_strip(self, source_key: str) -> float:
            raw_level = max(0.0, min(1.0, float(self.meter_levels.get(source_key, 0.0))))
            effective_gain = max(0.0, float(self._effective_strip_volume(source_key)) / 100.0)

            # Display the real sampled source, but scale the visible response by the strip's
            # current effective gain so raising the fader produces a more energetic meter.
            gain_scaled = raw_level * min(1.7, max(0.45, effective_gain))

            # Add a slight upward curve so moderate signals bounce more like a mixer meter.
            if gain_scaled > 0.0:
                gain_scaled = pow(min(1.0, gain_scaled), 0.78)

            if self.strip_mute_vars[source_key].get():
                gain_scaled *= 0.15
            return max(0.0, min(1.0, gain_scaled))

        def _update_selected_strip_summary(self) -> None:
            source_key = self.selected_strip_var.get()
            label = self.strip_label_vars[source_key].get().strip() or self.default_route_labels[source_key]
            self.selected_strip_label_var.set(label)
            self.selected_strip_volume_var.set(f"{int(self._effective_strip_volume(source_key))}% effective gain")
            active_targets = [target for target in self.ROUTE_TARGETS if self._route_enabled_for_strip(source_key, target)]
            self.selected_strip_routes_var.set(", ".join(active_targets) if active_targets else "No routes active")
            shortcut = self.selection_keybinds.get(source_key, "")
            hint_parts = [self.strip_subtitle_vars[source_key].get() or "Mixer strip"]
            if shortcut:
                hint_parts.append(f"Shortcut: {shortcut}")
            self.selected_strip_hint_var.set("  |  ".join(hint_parts))
            for key, frame in self.strip_card_frames.items():
                frame.configure(style="SelectedCard.TFrame" if key == source_key else "Card.TFrame")

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
            self.virtual_sources = dict(virtual_sources)

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
            self._refresh_strip_metadata()
            self._restart_meter_workers()
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
            self._update_selected_strip_summary()
            self.status_var.set(f"Selected fader: {self.selected_strip_label_var.get()}")

        def _ducking_config(self) -> dict[str, int | bool | str]:
            return {
                "enabled": bool(self.ducking_enabled_var.get()),
                "source": self.ducking_source_var.get(),
                "amount_percent": max(5, min(90, int(self.ducking_amount_var.get() or 35))),
                "threshold_percent": max(1, min(60, int(self.ducking_threshold_var.get() or 10))),
            }

        def _update_ducking_config(self) -> None:
            if self.loading_config:
                return
            self.backend.save_ducking_config(self._ducking_config())
            if self.ducking_active:
                self._apply_ducking_state(True)

        def _ducking_source_changed(self) -> None:
            self._update_ducking_config()
            if self.ducking_enabled_var.get():
                self._stop_ducking_monitor()
                self._start_ducking_monitor()

        def _ducking_source_name(self) -> str:
            source_label = self.ducking_source_var.get()
            if source_label == "Hardware In 2":
                return self.hw2_var.get()
            return self.hw1_var.get()

        def toggle_ducking(self) -> None:
            self._update_ducking_config()
            if self.ducking_enabled_var.get():
                self._start_ducking_monitor()
            else:
                self._stop_ducking_monitor()

        def _start_ducking_monitor(self) -> None:
            source_name = self._ducking_source_name()
            if not source_name:
                self.ducking_enabled_var.set(False)
                self.status_var.set("Select the ducking input source first.")
                self._update_ducking_config()
                return
            if self.ducking_thread is not None and self.ducking_thread.is_alive():
                return
            self.ducking_stop.clear()
            self.ducking_thread = threading.Thread(target=self._ducking_monitor_loop, daemon=True)
            self.ducking_thread.start()
            self.status_var.set(f"Ducking armed on {source_name}")

        def _stop_ducking_monitor(self) -> None:
            self.ducking_stop.set()
            self.ducking_thread = None
            if self.ducking_active:
                self._apply_ducking_state(False)

        def _ducking_monitor_loop(self) -> None:
            source_name = self._ducking_source_name()
            try:
                process = self._start_source_capture_process(source_name, 1)
            except OSError as exc:
                self.root.after(0, lambda: self._ducking_monitor_failed(f"Ducking unavailable: {exc}"))
                return

            release_seconds = 0.6
            last_active = 0.0
            active_state = False
            try:
                while not self.ducking_stop.is_set():
                    if process.stdout is None:
                        break
                    chunk = process.stdout.read(3200)
                    if not chunk:
                        break
                    level_percent = self._pcm_level_percent(chunk)
                    threshold = max(1, min(60, int(self.ducking_threshold_var.get() or 10)))
                    now = time.monotonic()
                    if level_percent >= threshold:
                        last_active = now
                    should_be_active = (now - last_active) <= release_seconds
                    if should_be_active != active_state:
                        active_state = should_be_active
                        self.root.after(0, lambda active_state=active_state: self._apply_ducking_state(active_state))
            finally:
                try:
                    process.terminate()
                except OSError:
                    pass
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        pass
                self.root.after(0, lambda: self._apply_ducking_state(False))

        def _pcm_level_percent(self, chunk: bytes) -> int:
            sample_count = len(chunk) // 2
            if sample_count <= 0:
                return 0
            samples = struct.unpack("<" + ("h" * sample_count), chunk[: sample_count * 2])
            rms = math.sqrt(sum(sample * sample for sample in samples) / sample_count)
            return int(round((rms / 32767.0) * 100))

        def _pcm_level_ratio(self, chunk: bytes) -> float:
            sample_count = len(chunk) // 2
            if sample_count <= 0:
                return 0.0
            samples = struct.unpack("<" + ("h" * sample_count), chunk[: sample_count * 2])
            peak = max(abs(sample) for sample in samples) / 32767.0
            rms = math.sqrt(sum(sample * sample for sample in samples) / sample_count) / 32767.0

            # Use a blended signal and map roughly -55 dBFS..0 dBFS into the visible meter range.
            # Raw linear RMS is too small for typical desktop playback and looks nearly inactive.
            signal = max(rms * 1.35, peak * 0.9)
            signal = max(0.000001, min(1.0, signal))
            floor_db = -55.0
            db = 20.0 * math.log10(signal)
            normalized = (db - floor_db) / abs(floor_db)
            return max(0.0, min(1.0, normalized))

        def _meter_source_name(self, source_key: str) -> str:
            if source_key == "hardware_input_1":
                return self.hw1_var.get().strip()
            if source_key == "hardware_input_2":
                return self.hw2_var.get().strip()
            return self.virtual_sources.get(source_key, "").strip()

        def _source_channel_count(self, source_name: str) -> int:
            if not source_name:
                return 0
            for source in self.backend.list_sources():
                if source.name == source_name:
                    return max(1, int(source.channels or 0))
            return 0

        def _meter_capture_channels(self, source_name: str) -> int:
            channels = self._source_channel_count(source_name)
            if channels <= 1:
                return 1
            return 2

        def _restart_meter_workers(self) -> None:
            desired_sources = {
                source_key: self._meter_source_name(source_key)
                for source_key, _label in self.route_rows
            }

            for source_key, worker in list(self.meter_workers.items()):
                desired_name = desired_sources.get(source_key, "")
                desired_channels = self._meter_capture_channels(desired_name) if desired_name else 0
                if (
                    desired_name == worker.source_name
                    and desired_channels == worker.channels
                    and worker.thread.is_alive()
                ):
                    continue
                self._stop_meter_worker(source_key)

            for source_key, source_name in desired_sources.items():
                if not source_name or source_key in self.meter_workers:
                    if not source_name:
                        self.meter_levels[source_key] = 0.0
                    continue
                channels = self._meter_capture_channels(source_name)
                stop_event = threading.Event()
                thread = threading.Thread(
                    target=self._meter_worker_loop,
                    args=(source_key, source_name, channels, stop_event),
                    daemon=True,
                )
                self.meter_workers[source_key] = self.MeterWorker(
                    stop=stop_event,
                    thread=thread,
                    source_name=source_name,
                    channels=channels,
                )
                thread.start()

        def _stop_meter_worker(self, source_key: str) -> None:
            worker = self.meter_workers.pop(source_key, None)
            if worker is None:
                return
            worker.stop.set()
            self.meter_levels[source_key] = 0.0

        def _stop_meter_workers(self) -> None:
            for source_key in list(self.meter_workers):
                self._stop_meter_worker(source_key)

        def _meter_worker_loop(
            self,
            source_key: str,
            source_name: str,
            channels: int,
            stop_event: threading.Event,
        ) -> None:
            process = None
            level = 0.0
            try:
                process = self._start_source_capture_process(source_name, max(1, channels))
                while not stop_event.is_set():
                    if process.stdout is None:
                        break
                    chunk = process.stdout.read(3200)
                    if not chunk:
                        break
                    target = self._pcm_level_ratio(chunk)
                    level += (target - level) * 0.45
                    self.meter_levels[source_key] = level
            except OSError:
                self.meter_levels[source_key] = 0.0
                return
            finally:
                self.meter_levels[source_key] = 0.0
                if process is not None:
                    try:
                        process.terminate()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                        except OSError:
                            pass

        def _start_source_capture_process(self, source_name: str, channels: int):
            if shutil.which("parecord"):
                return subprocess.Popen(
                    [
                        "parecord",
                        f"--device={source_name}",
                        "--raw",
                        "--rate=16000",
                        f"--channels={max(1, channels)}",
                        "--format=s16le",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env=os.environ.copy(),
                )
            return subprocess.Popen(
                [
                    "pw-record",
                    "--target",
                    source_name,
                    "--rate=16000",
                    f"--channels={max(1, channels)}",
                    "--format=s16",
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=os.environ.copy(),
            )

        def _ducking_monitor_failed(self, message: str) -> None:
            self.ducking_enabled_var.set(False)
            self.status_var.set(message)
            self._update_ducking_config()

        def _effective_strip_volume(self, source_key: str) -> int:
            base_volume = int(self.strip_volume_vars[source_key].get())
            if not self.ducking_active or source_key not in self.DUCKED_SOURCE_KEYS:
                return base_volume
            amount = max(5, min(90, int(self.ducking_amount_var.get() or 35)))
            return max(0, min(150, int(round(base_volume * (100 - amount) / 100))))

        def _app_assignment_source_key_to_label(self, source_key: str) -> str:
            return self.APP_ASSIGNMENT_LABELS.get(source_key, "")

        def _app_assignment_label_to_source_key(self, label: str) -> str:
            for source_key, source_label in self.APP_ASSIGNMENT_LABELS.items():
                if source_label == label:
                    return source_key
            return ""

        def _current_app_sink_label(self, sink_name: str) -> str:
            sink_labels = {
                "vm_system": "System Playback",
                "vm_input_1": "Input 1",
                "vm_input_2": "Input 2",
            }
            return sink_labels.get(sink_name, sink_name)

        def _has_active_solo(self) -> bool:
            return any(variable.get() for variable in self.strip_solo_vars.values())

        def _route_enabled_for_strip(self, source_key: str, target_key: str) -> bool:
            if self.strip_mute_vars[source_key].get():
                return False
            if self._has_active_solo() and not self.strip_solo_vars[source_key].get():
                return False
            return self.route_vars[source_key][target_key].get()

        def _apply_ducking_state(self, active: bool) -> None:
            self.ducking_active = active and bool(self.ducking_enabled_var.get())
            for source_key in self.DUCKED_SOURCE_KEYS:
                try:
                    self.backend.apply_live_strip_volume(source_key, self._effective_strip_volume(source_key))
                except AudioBackendError:
                    continue
            self._update_selected_strip_summary()
            if self.ducking_active:
                self.status_var.set("Ducking active")

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
            window.geometry("720x460")
            window.configure(bg=self.palette.bg)
            window.protocol("WM_DELETE_WINDOW", self.close_keybind_window)
            window.bind("<KeyPress>", self._capture_keybind)
            window.update_idletasks()
            window.deiconify()
            window.lift()
            window.focus_set()
            self.keybind_window = window
            self.keybind_value_vars = {}

            frame = ttk.Frame(window, style="Shell.TFrame", padding=20)
            frame.pack(fill="both", expand=True)
            frame.columnconfigure(1, weight=1)
            ttk.Label(frame, text="Keybinds", style="Title.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(
                frame,
                text="Assign strip selection and selected-fader volume shortcuts.",
                style="Subtitle.TLabel",
            ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 18))

            content = ttk.LabelFrame(frame, text="Bindings", style="Panel.TLabelframe", padding=16)
            content.grid(row=2, column=0, columnspan=4, sticky="nsew")
            content.columnconfigure(1, weight=1)
            ttk.Label(content, text="Strip", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(content, text="Shortcut", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0))

            for row_index, (source_key, default_label) in enumerate(self.route_rows, start=1):
                label = self.strip_label_vars[source_key].get().strip() or default_label
                ttk.Label(content, text=label, style="Body.TLabel").grid(row=row_index, column=0, sticky="w", pady=(8, 0))
                value_var = tk.StringVar(value=self.selection_keybinds.get(source_key, ""))
                self.keybind_value_vars[source_key] = value_var
                ttk.Label(content, textvariable=value_var, width=18, style="Body.TLabel").grid(
                    row=row_index, column=1, sticky="w", padx=(12, 12), pady=(8, 0)
                )
                ttk.Button(
                    content,
                    text="Set Key",
                    command=lambda source_key=source_key: self.begin_key_capture("select", source_key),
                    style="Accent.TButton",
                ).grid(row=row_index, column=2, pady=(8, 0))
                ttk.Button(
                    content,
                    text="Clear",
                    command=lambda source_key=source_key: self.clear_selection_keybind(source_key),
                    style="Ghost.TButton",
                ).grid(row=row_index, column=3, padx=(8, 0), pady=(8, 0))

            volume_row = len(self.route_rows) + 2
            ttk.Label(content, text="Volume Controls", style="Section.TLabel").grid(
                row=volume_row, column=0, sticky="w", pady=(16, 0)
            )

            up_row = volume_row + 1
            self.volume_up_var = tk.StringVar(value=str(self.volume_keybinds.get("up", "")))
            ttk.Label(content, text="Selected Fader Up", style="Body.TLabel").grid(
                row=up_row, column=0, sticky="w", pady=(8, 0)
            )
            ttk.Label(content, textvariable=self.volume_up_var, width=18, style="Body.TLabel").grid(
                row=up_row, column=1, sticky="w", padx=(12, 12), pady=(8, 0)
            )
            ttk.Button(content, text="Set Key", command=lambda: self.begin_key_capture("volume", "up"), style="Accent.TButton").grid(
                row=up_row, column=2, pady=(8, 0)
            )
            ttk.Button(content, text="Clear", command=lambda: self.clear_volume_keybind("up"), style="Ghost.TButton").grid(
                row=up_row, column=3, padx=(8, 0), pady=(8, 0)
            )

            down_row = volume_row + 2
            self.volume_down_var = tk.StringVar(value=str(self.volume_keybinds.get("down", "")))
            ttk.Label(content, text="Selected Fader Down", style="Body.TLabel").grid(
                row=down_row, column=0, sticky="w", pady=(8, 0)
            )
            ttk.Label(content, textvariable=self.volume_down_var, width=18, style="Body.TLabel").grid(
                row=down_row, column=1, sticky="w", padx=(12, 12), pady=(8, 0)
            )
            ttk.Button(content, text="Set Key", command=lambda: self.begin_key_capture("volume", "down"), style="Accent.TButton").grid(
                row=down_row, column=2, pady=(8, 0)
            )
            ttk.Button(content, text="Clear", command=lambda: self.clear_volume_keybind("down"), style="Ghost.TButton").grid(
                row=down_row, column=3, padx=(8, 0), pady=(8, 0)
            )

            step_row = volume_row + 3
            self.volume_step_var = tk.IntVar(value=int(self.volume_keybinds.get("step_percent", 5)))
            ttk.Label(content, text="Step %", style="Body.TLabel").grid(row=step_row, column=0, sticky="w", pady=(8, 0))
            ttk.Spinbox(
                content,
                from_=1,
                to=25,
                textvariable=self.volume_step_var,
                width=6,
                command=self.save_volume_keybind_settings,
            ).grid(row=step_row, column=1, sticky="w", padx=(12, 0), pady=(8, 0))
            self.volume_step_trace_id = self.volume_step_var.trace_add("write", lambda *_args: self.save_volume_keybind_settings())

            self.keybind_status_var = tk.StringVar(value="Click Set Key, then press the shortcut you want.")
            ttk.Label(content, textvariable=self.keybind_status_var, justify="left", style="Muted.TLabel").grid(
                row=step_row + 1, column=0, columnspan=4, sticky="w", pady=(16, 0)
            )
            self.status_var.set("Keybind window opened.")

        def open_ducking_window(self) -> None:
            if self.ducking_window is not None and self.ducking_window.winfo_exists():
                self.ducking_window.deiconify()
                self.ducking_window.update_idletasks()
                self.ducking_window.lift()
                self.ducking_window.focus_set()
                self.status_var.set("Ducking window opened.")
                return

            window = tk.Toplevel(self.root)
            window.title("Ducking")
            window.geometry("520x320")
            window.configure(bg=self.palette.bg)
            window.protocol("WM_DELETE_WINDOW", self.close_ducking_window)
            window.update_idletasks()
            window.deiconify()
            window.lift()
            window.focus_set()
            self.ducking_window = window

            frame = ttk.Frame(window, style="Shell.TFrame", padding=20)
            frame.pack(fill="both", expand=True)
            ttk.Label(frame, text="Ducking", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
            ttk.Label(
                frame,
                text="Lower playback lanes while the selected microphone crosses the trigger threshold.",
                style="Subtitle.TLabel",
            ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 18))

            panel = ttk.LabelFrame(frame, text="Mic Trigger", style="Panel.TLabelframe", padding=16)
            panel.grid(row=2, column=0, columnspan=2, sticky="nsew")

            ttk.Checkbutton(
                panel,
                text="Enable ducking while mic is active",
                variable=self.ducking_enabled_var,
                command=self.toggle_ducking,
            ).grid(row=0, column=0, columnspan=2, sticky="w")

            ttk.Label(panel, text="Trigger Input", style="Body.TLabel").grid(row=1, column=0, sticky="w", pady=(12, 0))
            ducking_source_combo = ttk.Combobox(
                panel,
                textvariable=self.ducking_source_var,
                state="readonly",
                values=["Hardware In 1", "Hardware In 2"],
                width=18,
            )
            ducking_source_combo.grid(row=1, column=1, sticky="w", padx=(12, 0), pady=(12, 0))
            ducking_source_combo.bind("<<ComboboxSelected>>", lambda _event: self._ducking_source_changed())

            ttk.Label(panel, text="Duck Amount %", style="Body.TLabel").grid(row=2, column=0, sticky="w", pady=(12, 0))
            ttk.Spinbox(
                panel,
                from_=5,
                to=90,
                textvariable=self.ducking_amount_var,
                width=6,
                command=self._update_ducking_config,
            ).grid(row=2, column=1, sticky="w", padx=(12, 0), pady=(12, 0))

            ttk.Label(panel, text="Threshold %", style="Body.TLabel").grid(row=3, column=0, sticky="w", pady=(12, 0))
            ttk.Spinbox(
                panel,
                from_=1,
                to=60,
                textvariable=self.ducking_threshold_var,
                width=6,
                command=self._update_ducking_config,
            ).grid(row=3, column=1, sticky="w", padx=(12, 0), pady=(12, 0))

            ttk.Label(
                panel,
                text="Ducking lowers System Playback, Input 1, and Input 2 while the chosen mic is active.",
                style="Muted.TLabel",
                justify="left",
                wraplength=380,
            ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(16, 0))

            self.status_var.set("Ducking window opened.")

        def open_apps_window(self) -> None:
            if self.apps_window is not None and self.apps_window.winfo_exists():
                self.apps_window.deiconify()
                self.apps_window.update_idletasks()
                self.apps_window.lift()
                self.apps_window.focus_set()
                self.refresh_apps_window()
                self.status_var.set("Apps window opened.")
                return

            window = tk.Toplevel(self.root)
            window.title("Apps")
            window.geometry("1040x560")
            window.configure(bg=self.palette.bg)
            window.protocol("WM_DELETE_WINDOW", self.close_apps_window)
            window.update_idletasks()
            window.deiconify()
            window.lift()
            window.focus_set()
            self.apps_window = window

            outer = ttk.Frame(window, style="Shell.TFrame", padding=20)
            outer.pack(fill="both", expand=True)
            outer.columnconfigure(0, weight=1)

            ttk.Label(outer, text="Apps", style="Title.TLabel").pack(anchor="w")
            ttk.Label(
                outer,
                text="Route active playback streams to VM_System, VM_Input_1, or VM_Input_2 without changing backend behavior.",
                style="Subtitle.TLabel",
            ).pack(anchor="w", pady=(4, 18))

            controls = ttk.Frame(outer, style="Toolbar.TFrame")
            controls.pack(fill="x")
            ttk.Button(controls, text="Refresh", command=self.refresh_apps_window, style="Ghost.TButton").pack(side="left")
            ttk.Button(controls, text="Apply Saved Rules", command=self.apply_saved_app_assignments, style="Accent.TButton").pack(
                side="left", padx=(8, 0)
            )

            self.apps_content_frame = ttk.Frame(outer, style="Surface.TFrame")
            self.apps_content_frame.pack(fill="both", expand=True, pady=(12, 0))
            self.refresh_apps_window()
            self.status_var.set("Apps window opened.")

        def refresh_apps_window(self) -> None:
            if self.apps_content_frame is None:
                return
            for child in self.apps_content_frame.winfo_children():
                child.destroy()

            try:
                streams = self.backend.list_app_streams()
            except AudioBackendError as exc:
                ttk.Label(self.apps_content_frame, text=str(exc), justify="left", style="Muted.TLabel").pack(anchor="w")
                self.status_var.set(str(exc))
                return

            header = ttk.Frame(self.apps_content_frame, style="Panel.TFrame", padding=12)
            header.pack(fill="x")
            ttk.Label(header, text="App", width=22, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(header, text="Stream", width=28, style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0))
            ttk.Label(header, text="Current Sink", width=34, style="Muted.TLabel").grid(row=0, column=2, sticky="w", padx=(12, 0))
            ttk.Label(header, text="Assign To", width=18, style="Muted.TLabel").grid(row=0, column=3, sticky="w", padx=(12, 0))

            if not streams:
                ttk.Label(
                    self.apps_content_frame,
                    text="No active playback app streams found.",
                    justify="left",
                    style="Muted.TLabel",
                ).pack(anchor="w", pady=(12, 0))
                return

            options = list(self.APP_ASSIGNMENT_LABELS.values())
            for stream in streams:
                row = ttk.Frame(self.apps_content_frame, style="Panel.TFrame", padding=12)
                row.pack(fill="x", pady=(8, 0))

                assignment_value = self._app_assignment_source_key_to_label(self.app_assignments.get(stream.app_id, ""))
                if not assignment_value:
                    assignment_value = self._current_app_sink_label(stream.sink_name)
                assignment_var = tk.StringVar(value=assignment_value)

                ttk.Label(row, text=stream.app_name, width=22, style="Body.TLabel").grid(row=0, column=0, sticky="w")
                ttk.Label(row, text=stream.stream_name, width=28, style="Body.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0))
                ttk.Label(
                    row,
                    text=self._current_app_sink_label(stream.sink_name) or "unknown",
                    width=34,
                    style="Muted.TLabel",
                ).grid(row=0, column=2, sticky="w", padx=(12, 0))
                combo = ttk.Combobox(row, textvariable=assignment_var, values=options, state="readonly", width=16)
                combo.grid(row=0, column=3, sticky="w", padx=(12, 0))
                ttk.Button(
                    row,
                    text="Assign",
                    command=lambda stream=stream, assignment_var=assignment_var: self.assign_app_stream(stream, assignment_var),
                    style="Accent.TButton",
                ).grid(row=0, column=4, padx=(12, 0))
                ttk.Button(
                    row,
                    text="Clear Rule",
                    command=lambda app_id=stream.app_id: self.clear_app_assignment(app_id),
                    style="Ghost.TButton",
                ).grid(row=0, column=5, padx=(8, 0))

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

        def close_ducking_window(self) -> None:
            if self.ducking_window is not None and self.ducking_window.winfo_exists():
                self.ducking_window.destroy()
            self.ducking_window = None

        def close_apps_window(self) -> None:
            if self.apps_window is not None and self.apps_window.winfo_exists():
                self.apps_window.destroy()
            self.apps_window = None
            self.apps_content_frame = None

        def assign_app_stream(self, stream, assignment_var) -> None:
            source_key = self._app_assignment_label_to_source_key(assignment_var.get())
            if not source_key:
                self.status_var.set("Select an app target first.")
                return
            try:
                self.backend.move_app_stream_to_sink(
                    stream.stream_id,
                    self.backend._normalize_app_assignment_target(source_key),
                )
                self.app_assignments[stream.app_id] = source_key
                self.backend.save_app_assignments(self.app_assignments)
            except AudioBackendError as exc:
                self._show_backend_error(str(exc))
                return
            self.status_var.set(f"Assigned {stream.app_name} to {assignment_var.get()}")
            self.refresh_apps_window()

        def clear_app_assignment(self, app_id: str) -> None:
            if app_id in self.app_assignments:
                self.app_assignments.pop(app_id, None)
                self.backend.save_app_assignments(self.app_assignments)
            self.status_var.set(f"Cleared saved app rule for {app_id}")
            self.refresh_apps_window()

        def apply_saved_app_assignments(self) -> None:
            try:
                moved = self.backend.apply_app_assignments(self.app_assignments)
            except AudioBackendError as exc:
                self._show_backend_error(str(exc))
                return
            self.status_var.set(f"Applied app rules to {moved} stream(s)")
            if self.apps_window is not None and self.apps_window.winfo_exists():
                self.refresh_apps_window()

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
            ducking = config.get("ducking", {})
            self.app_assignments = {
                app_id: str(target_value)
                for app_id, target_value in config.get("app_assignments", {}).items()
                if self.backend._normalize_app_assignment_target(str(target_value))
            }

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
                if source_key in self.strip_solo_vars:
                    self.strip_solo_vars[source_key].set(bool(settings.get("solo", False)))
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
            self.ducking_enabled_var.set(bool(ducking.get("enabled", False)))
            source_label = ducking.get("source", "Hardware In 1")
            if source_label in {"Hardware In 1", "Hardware In 2"}:
                self.ducking_source_var.set(source_label)
            try:
                self.ducking_amount_var.set(int(ducking.get("amount_percent", 35)))
            except (TypeError, ValueError):
                self.ducking_amount_var.set(35)
            try:
                self.ducking_threshold_var.set(int(ducking.get("threshold_percent", 10)))
            except (TypeError, ValueError):
                self.ducking_threshold_var.set(10)
            if self.ducking_enabled_var.get():
                self._start_ducking_monitor()
            if self.app_assignments:
                try:
                    self.backend.apply_app_assignments(self.app_assignments)
                except AudioBackendError as exc:
                    self.status_var.set(str(exc))
            self._refresh_strip_metadata()
            self._restart_meter_workers()

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
                matrix_data[source_key] = {
                    target_key: self._route_enabled_for_strip(source_key, target_key)
                    for target_key in targets
                }
            matrix = RoutingMatrix(routes=matrix_data)
            targets = RouteTargetSelection(a1_sink=self.a1_var.get(), a2_sink=self.a2_var.get())
            strip_settings = {
                source_key: {
                    "label": self.strip_label_vars[source_key].get().strip() or default_label,
                    "muted": self.strip_mute_vars[source_key].get(),
                    "solo": self.strip_solo_vars[source_key].get(),
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
            try:
                self.backend.apply_app_assignments(self.app_assignments)
            except AudioBackendError as exc:
                messagebox.showerror("Audio backend error", str(exc))
                self.status_var.set(str(exc))
                return
            if self.ducking_enabled_var.get():
                self._apply_ducking_state(self.ducking_active)

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
            self.strip_volume_readout_vars[source_key].set(f"{int(volume)}%")
            if self.ducking_enabled_var.get() and source_key in self.DUCKED_SOURCE_KEYS:
                try:
                    self.backend.apply_live_strip_volume(source_key, self._effective_strip_volume(source_key))
                except AudioBackendError as exc:
                    self._show_backend_error(str(exc))
            self._update_selected_strip_summary()

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
            self.close_ducking_window()
            self.close_apps_window()
            self._stop_ducking_monitor()
            self._stop_meter_workers()
            self._stop_command_server()
            self.root.destroy()

    root = tk.Tk()
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
