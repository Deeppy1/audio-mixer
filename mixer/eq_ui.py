from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .eq import (
    MAX_FREQUENCY,
    MAX_GAIN_DB,
    MAX_Q,
    MIN_FREQUENCY,
    MIN_GAIN_DB,
    MIN_Q,
    EQState,
    build_response_curve,
    clamp,
    default_presets,
    freq_to_normalized,
    normalized_to_freq,
)
from .ui_theme import PALETTE, MixerPalette
from .ui_widgets import NeonToggle


class EQGraphCanvas(tk.Canvas):
    def __init__(self, master, *, palette: MixerPalette = PALETTE, change_callback=None, select_callback=None) -> None:
        super().__init__(
            master,
            bg=palette.bg_alt,
            bd=0,
            highlightthickness=1,
            highlightbackground=palette.border,
            width=760,
            height=340,
            cursor="crosshair",
        )
        self.palette = palette
        self.change_callback = change_callback
        self.select_callback = select_callback
        self.state = EQState.default()
        self.selected_band_index = 0
        self._dragging_band_index: int | None = None
        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def set_state(self, state: EQState, selected_band_index: int) -> None:
        self.state = state
        self.selected_band_index = max(0, min(selected_band_index, len(state.bands) - 1))
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width = max(480, int(self.winfo_width() or self["width"]))
        height = max(240, int(self.winfo_height() or self["height"]))
        left = 52
        top = 18
        right = width - 20
        bottom = height - 34
        self._draw_grid(left, top, right, bottom)
        self._draw_response(left, top, right, bottom)
        self._draw_band_points(left, top, right, bottom)

    def _draw_grid(self, left: int, top: int, right: int, bottom: int) -> None:
        self.create_rectangle(left, top, right, bottom, outline=self.palette.border, width=1)
        frequencies = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]
        for frequency in frequencies:
            x = self._freq_to_x(frequency, left, right)
            self.create_line(x, top, x, bottom, fill=self.palette.border, dash=(2, 4))
            label = f"{int(frequency / 1000)}k" if frequency >= 1000 else str(int(frequency))
            self.create_text(x, bottom + 14, text=label, fill=self.palette.text_dim, font=("DejaVu Sans", 8))

        for gain in (-18, -12, -6, 0, 6, 12, 18):
            y = self._gain_to_y(gain, top, bottom)
            line_color = self.palette.accent if gain == 0 else self.palette.border
            dash = () if gain == 0 else (2, 4)
            self.create_line(left, y, right, y, fill=line_color, dash=dash)
            prefix = "+" if gain > 0 else ""
            self.create_text(left - 10, y, text=f"{prefix}{gain}", fill=self.palette.text_dim, font=("DejaVu Sans", 8))

    def _draw_response(self, left: int, top: int, right: int, bottom: int) -> None:
        response = build_response_curve(self.state)
        coords: list[float] = []
        for frequency, gain_db in response:
            coords.extend((self._freq_to_x(frequency, left, right), self._gain_to_y(gain_db, top, bottom)))
        fill_coords = [left, self._gain_to_y(0.0, top, bottom)]
        fill_coords.extend(coords)
        fill_coords.extend([right, self._gain_to_y(0.0, top, bottom)])
        self.create_polygon(fill_coords, fill="#10365A", outline="", smooth=True, splinesteps=24)
        self.create_line(*coords, fill=self.palette.accent_bright, width=3, smooth=True, splinesteps=24)

    def _draw_band_points(self, left: int, top: int, right: int, bottom: int) -> None:
        band_colors = [
            self.palette.accent,
            self.palette.accent_bright,
            self.palette.warning,
            self.palette.accent_soft,
            "#C45EFF",
        ]
        for index, band in enumerate(self.state.bands):
            x = self._freq_to_x(band.frequency, left, right)
            y = self._gain_to_y(band.gain_db, top, bottom)
            radius = 8 if index == self.selected_band_index else 6
            outline = self.palette.text if index == self.selected_band_index else band_colors[index % len(band_colors)]
            fill = outline if band.enabled else self.palette.panel_selected
            self.create_oval(x - radius, y - radius, x + radius, y + radius, fill=fill, outline=outline, width=2)
            self.create_text(x, y - 16, text=band.label, fill=outline, font=("DejaVu Sans", 8, "bold"))

    def _on_press(self, event) -> None:
        hit = self._band_at(event.x, event.y)
        if hit is None:
            return
        self.selected_band_index = hit
        self._dragging_band_index = hit
        if self.select_callback is not None:
            self.select_callback(hit)
        self.redraw()

    def _on_drag(self, event) -> None:
        if self._dragging_band_index is None:
            return
        band = self.state.bands[self._dragging_band_index]
        width = max(480, int(self.winfo_width() or self["width"]))
        height = max(240, int(self.winfo_height() or self["height"]))
        left = 52
        top = 18
        right = width - 20
        bottom = height - 34

        normalized = clamp((event.x - left) / max(1, right - left), 0.0, 1.0)
        gain_ratio = clamp((event.y - top) / max(1, bottom - top), 0.0, 1.0)
        frequency = normalized_to_freq(normalized)
        gain_db = MAX_GAIN_DB - (gain_ratio * (MAX_GAIN_DB - MIN_GAIN_DB))

        minimum = MIN_FREQUENCY if self._dragging_band_index == 0 else self.state.bands[self._dragging_band_index - 1].frequency * 1.1
        maximum = MAX_FREQUENCY if self._dragging_band_index == len(self.state.bands) - 1 else self.state.bands[self._dragging_band_index + 1].frequency / 1.1
        band.frequency = clamp(frequency, minimum, maximum)
        band.gain_db = clamp(gain_db, MIN_GAIN_DB, MAX_GAIN_DB)
        self.state.normalize_band_order()
        if self.change_callback is not None:
            self.change_callback()
        self.redraw()

    def _on_release(self, _event) -> None:
        self._dragging_band_index = None

    def _band_at(self, x: int, y: int) -> int | None:
        width = max(480, int(self.winfo_width() or self["width"]))
        height = max(240, int(self.winfo_height() or self["height"]))
        left = 52
        top = 18
        right = width - 20
        bottom = height - 34
        best_index = None
        best_distance = 18.0
        for index, band in enumerate(self.state.bands):
            bx = self._freq_to_x(band.frequency, left, right)
            by = self._gain_to_y(band.gain_db, top, bottom)
            distance = ((bx - x) ** 2 + (by - y) ** 2) ** 0.5
            if distance <= best_distance:
                best_index = index
                best_distance = distance
        return best_index

    def _freq_to_x(self, frequency: float, left: int, right: int) -> float:
        return left + freq_to_normalized(frequency) * (right - left)

    def _gain_to_y(self, gain_db: float, top: int, bottom: int) -> float:
        ratio = (MAX_GAIN_DB - clamp(gain_db, MIN_GAIN_DB, MAX_GAIN_DB)) / (MAX_GAIN_DB - MIN_GAIN_DB)
        return top + ratio * (bottom - top)


class ParametricEQWindow:
    def __init__(self, root: tk.Misc, *, palette: MixerPalette = PALETTE, on_change=None) -> None:
        self.root = root
        self.palette = palette
        self.on_change = on_change
        self.presets = default_presets()
        self.window: tk.Toplevel | None = None
        self.state: EQState | None = None
        self.source_key = ""
        self.selected_band_index = 0
        self.strip_label = ""
        self.preset_var = tk.StringVar(value="Flat")
        self.band_enabled_var = tk.BooleanVar(value=True)
        self.bypass_var = tk.BooleanVar(value=False)
        self.band_name_var = tk.StringVar(value="Band 1")
        self.freq_var = tk.DoubleVar(value=1000.0)
        self.gain_var = tk.DoubleVar(value=0.0)
        self.q_var = tk.DoubleVar(value=1.0)
        self.eq_title_var = tk.StringVar(value="Parametric EQ")
        self.eq_subtitle_var = tk.StringVar(value="")
        self.graph: EQGraphCanvas | None = None
        self.freq_scale: ttk.Scale | None = None
        self.gain_scale: ttk.Scale | None = None
        self.q_scale: ttk.Scale | None = None
        self.band_toggle: NeonToggle | None = None
        self._updating_controls = False

    def open_for_strip(self, source_key: str, strip_label: str, state: EQState) -> None:
        self.source_key = source_key
        self.strip_label = strip_label
        self.state = state
        if self.window is None or not self.window.winfo_exists():
            self._build()
        self._update_title()
        self._sync_controls_from_state()
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def close(self) -> None:
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()
        self.window = None

    def _build(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("Parametric EQ")
        window.geometry("1180x680")
        window.minsize(980, 600)
        window.configure(bg=self.palette.bg)
        window.protocol("WM_DELETE_WINDOW", self.close)
        self.window = window

        shell = ttk.Frame(window, style="Shell.TFrame", padding=20)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=3)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ttk.Frame(shell, style="Toolbar.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 16))
        header.columnconfigure(0, weight=1)
        title_box = ttk.Frame(header, style="Toolbar.TFrame")
        title_box.grid(row=0, column=0, sticky="w")
        ttk.Label(title_box, textvariable=self.eq_title_var, style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, textvariable=self.eq_subtitle_var, style="Subtitle.TLabel").pack(anchor="w", pady=(4, 0))

        actions = ttk.Frame(header, style="Toolbar.TFrame")
        actions.grid(row=0, column=1, sticky="e")
        ttk.Label(actions, text="Preset", style="Muted.TLabel").pack(side="left", padx=(0, 8))
        preset_combo = ttk.Combobox(
            actions,
            textvariable=self.preset_var,
            state="readonly",
            width=18,
            values=tuple(list(self.presets) + ["Custom"]),
        )
        preset_combo.pack(side="left", padx=(0, 10))
        preset_combo.bind("<<ComboboxSelected>>", lambda _event: self._apply_preset())
        NeonToggle(
            actions,
            text="Bypass",
            variable=self.bypass_var,
            palette=self.palette,
            on_color=self.palette.warning,
            width=8,
            command=self._toggle_bypass,
        ).pack(side="left", padx=(0, 10))
        ttk.Button(actions, text="Reset", command=self._reset_state, style="Ghost.TButton").pack(side="left")

        graph_panel = ttk.Frame(shell, style="Panel.TFrame", padding=16)
        graph_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 16))
        graph_panel.columnconfigure(0, weight=1)
        graph_panel.rowconfigure(0, weight=1)

        self.graph = EQGraphCanvas(
            graph_panel,
            palette=self.palette,
            change_callback=self._handle_graph_change,
            select_callback=self._select_band,
        )
        self.graph.grid(row=0, column=0, sticky="nsew")

        side = ttk.Frame(shell, style="Panel.TFrame", padding=16)
        side.grid(row=1, column=1, sticky="nsew")
        side.columnconfigure(0, weight=1)

        band_panel = ttk.LabelFrame(side, text="Band Focus", style="Panel.TLabelframe", padding=14)
        band_panel.grid(row=0, column=0, sticky="ew")
        band_panel.columnconfigure(0, weight=1)
        ttk.Label(band_panel, textvariable=self.band_name_var, style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.band_toggle = NeonToggle(
            band_panel,
            text="Active",
            variable=self.band_enabled_var,
            palette=self.palette,
            on_color=self.palette.success,
            width=7,
            command=self._toggle_band_enabled,
        )
        self.band_toggle.grid(row=0, column=1, sticky="e")

        ttk.Label(band_panel, text="Frequency", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(14, 0))
        self.freq_scale = ttk.Scale(
            band_panel,
            from_=0.0,
            to=1.0,
            variable=self.freq_var,
            style="Mixer.Horizontal.TScale",
            command=self._on_frequency_scale,
        )
        self.freq_scale.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.freq_readout = ttk.Label(band_panel, text="", style="Body.TLabel")
        self.freq_readout.grid(row=3, column=0, sticky="w", pady=(6, 0))

        ttk.Label(band_panel, text="Gain", style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=(14, 0))
        self.gain_scale = ttk.Scale(
            band_panel,
            from_=MIN_GAIN_DB,
            to=MAX_GAIN_DB,
            variable=self.gain_var,
            style="Mixer.Horizontal.TScale",
            command=self._on_gain_scale,
        )
        self.gain_scale.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.gain_readout = ttk.Label(band_panel, text="", style="Body.TLabel")
        self.gain_readout.grid(row=6, column=0, sticky="w", pady=(6, 0))

        ttk.Label(band_panel, text="Q", style="Muted.TLabel").grid(row=7, column=0, sticky="w", pady=(14, 0))
        self.q_scale = ttk.Scale(
            band_panel,
            from_=MIN_Q,
            to=MAX_Q,
            variable=self.q_var,
            style="Mixer.Horizontal.TScale",
            command=self._on_q_scale,
        )
        self.q_scale.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.q_readout = ttk.Label(band_panel, text="", style="Body.TLabel")
        self.q_readout.grid(row=9, column=0, sticky="w", pady=(6, 0))

        help_panel = ttk.LabelFrame(side, text="Workflow", style="Panel.TLabelframe", padding=14)
        help_panel.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        ttk.Label(
            help_panel,
            text=(
                "Drag points directly on the graph to shape tone.\n"
                "Use the inspector for precise gain and Q moves.\n"
                "Presets are saved per strip with the mixer session."
            ),
            style="Muted.TLabel",
            justify="left",
            wraplength=260,
        ).grid(row=0, column=0, sticky="w")

    def _current_band(self):
        if self.state is None:
            return None
        return self.state.bands[self.selected_band_index]

    def _select_band(self, index: int) -> None:
        if self.state is None:
            return
        self.selected_band_index = max(0, min(index, len(self.state.bands) - 1))
        self._sync_controls_from_state()

    def _sync_controls_from_state(self) -> None:
        if self.state is None:
            return
        self._updating_controls = True
        band = self._current_band()
        if band is None:
            self._updating_controls = False
            return
        self.preset_var.set(self.state.preset_name)
        self.bypass_var.set(self.state.bypass)
        self.band_enabled_var.set(band.enabled)
        self.band_name_var.set(f"{band.label}  |  {band.kind}")
        self.freq_var.set(freq_to_normalized(band.frequency))
        self.gain_var.set(band.gain_db)
        self.q_var.set(band.q)
        self.freq_readout.configure(text=f"{int(round(band.frequency))} Hz")
        gain_prefix = "+" if band.gain_db > 0 else ""
        self.gain_readout.configure(text=f"{gain_prefix}{band.gain_db:.1f} dB")
        self.q_readout.configure(text=f"Q {band.q:.2f}")
        self._updating_controls = False
        if self.graph is not None:
            self.graph.set_state(self.state, self.selected_band_index)

    def _update_title(self) -> None:
        self.eq_title_var.set(f"Parametric EQ  |  {self.strip_label}")
        self.eq_subtitle_var.set("Five-band parametric curve editor with live response preview.")

    def _emit_change(self) -> None:
        if self.state is None:
            return
        self.state.normalize_band_order()
        self._sync_controls_from_state()
        if self.on_change is not None:
            self.on_change(self.source_key, self.state)

    def _handle_graph_change(self) -> None:
        self._emit_change()

    def _on_frequency_scale(self, _value: str) -> None:
        if self._updating_controls or self.state is None:
            return
        band = self._current_band()
        if band is None:
            return
        band.frequency = normalized_to_freq(self.freq_var.get())
        self.state.preset_name = "Custom"
        self._emit_change()

    def _on_gain_scale(self, _value: str) -> None:
        if self._updating_controls or self.state is None:
            return
        band = self._current_band()
        if band is None:
            return
        band.gain_db = clamp(self.gain_var.get(), MIN_GAIN_DB, MAX_GAIN_DB)
        self.state.preset_name = "Custom"
        self._emit_change()

    def _on_q_scale(self, _value: str) -> None:
        if self._updating_controls or self.state is None:
            return
        band = self._current_band()
        if band is None:
            return
        band.q = clamp(self.q_var.get(), MIN_Q, MAX_Q)
        self.state.preset_name = "Custom"
        self._emit_change()

    def _toggle_bypass(self) -> None:
        if self._updating_controls or self.state is None:
            return
        self.state.bypass = bool(self.bypass_var.get())
        self._emit_change()

    def _toggle_band_enabled(self) -> None:
        if self._updating_controls or self.state is None:
            return
        band = self._current_band()
        if band is None:
            return
        band.enabled = bool(self.band_enabled_var.get())
        self.state.preset_name = "Custom"
        self._emit_change()

    def _apply_preset(self) -> None:
        if self.state is None:
            return
        preset = self.presets.get(self.preset_var.get())
        if preset is None:
            return
        replacement = preset.copy()
        self.state.preset_name = replacement.preset_name
        self.state.bypass = replacement.bypass
        self.state.bands = replacement.bands
        self.selected_band_index = min(self.selected_band_index, len(self.state.bands) - 1)
        self._emit_change()

    def _reset_state(self) -> None:
        if self.state is None:
            return
        replacement = EQState.default()
        self.state.preset_name = replacement.preset_name
        self.state.bypass = replacement.bypass
        self.state.bands = replacement.bands
        self.selected_band_index = 0
        self._emit_change()
