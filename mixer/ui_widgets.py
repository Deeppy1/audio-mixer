from __future__ import annotations

import tkinter as tk

from .ui_theme import MixerPalette, PALETTE


class NeonToggle(tk.Checkbutton):
    def __init__(
        self,
        master,
        *,
        text: str,
        variable: tk.BooleanVar,
        palette: MixerPalette = PALETTE,
        on_color: str | None = None,
        off_color: str | None = None,
        text_on: str = "#06111F",
        text_off: str | None = None,
        width: int = 6,
        command=None,
        **kwargs,
    ) -> None:
        self.variable = variable
        self.palette = palette
        self.on_color = on_color or palette.accent
        self.off_color = off_color or palette.panel
        self.text_on = text_on
        self.text_off = text_off or palette.text_muted
        super().__init__(
            master,
            text=text,
            variable=variable,
            indicatoron=False,
            selectcolor=self.on_color,
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            padx=10,
            pady=6,
            width=width,
            font=("DejaVu Sans Condensed", 9, "bold"),
            cursor="hand2",
            command=command,
            **kwargs,
        )
        self.variable.trace_add("write", lambda *_args: self._sync_state())
        self.bind("<Enter>", lambda _event: self.configure(highlightbackground=self.palette.border_glow))
        self.bind("<Leave>", lambda _event: self._sync_state())
        self._sync_state()

    def _sync_state(self) -> None:
        selected = bool(self.variable.get())
        bg = self.on_color if selected else self.off_color
        fg = self.text_on if selected else self.text_off
        self.configure(
            bg=bg,
            fg=fg,
            activebackground=bg,
            activeforeground=fg,
            disabledforeground=self.palette.text_dim,
            highlightbackground=self.on_color if selected else self.palette.border,
            highlightcolor=self.on_color if selected else self.palette.border_glow,
        )


class AnimatedLevelMeter(tk.Canvas):
    def __init__(
        self,
        master,
        *,
        level_getter,
        orientation: str = "vertical",
        width: int = 24,
        height: int = 180,
        segments: int = 16,
        palette: MixerPalette = PALETTE,
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            bg=palette.bg_alt,
            bd=0,
            highlightthickness=1,
            highlightbackground=palette.border,
            **kwargs,
        )
        self.level_getter = level_getter
        self.orientation = orientation
        self.segments = max(4, segments)
        self.palette = palette
        self._display_level = 0.0
        self._peak_level = 0.0
        self._running = True
        self.after(90, self._tick)

    def stop(self) -> None:
        self._running = False

    def destroy(self) -> None:
        self._running = False
        super().destroy()

    def _tick(self) -> None:
        if not self._running:
            return
        try:
            raw_level = float(self.level_getter())
        except Exception:
            raw_level = 0.0
        target = max(0.0, min(1.0, raw_level))
        self._display_level += (target - self._display_level) * 0.35
        self._peak_level = max(target, self._peak_level * 0.94)
        self._draw_meter()
        self.after(90, self._tick)

    def _draw_meter(self) -> None:
        self.delete("all")
        width = max(1, int(self.winfo_width() or self["width"]))
        height = max(1, int(self.winfo_height() or self["height"]))
        gap = 2 if self.orientation == "vertical" else 1
        self.create_rectangle(0, 0, width, height, fill=self.palette.bg_alt, outline=self.palette.border)

        for index in range(self.segments):
            if self.orientation == "vertical":
                segment_height = (height - gap * (self.segments - 1)) / self.segments
                y2 = height - index * (segment_height + gap)
                y1 = y2 - segment_height
                x1, x2 = 4, width - 4
            else:
                segment_width = (width - 6 - gap * (self.segments - 1)) / self.segments
                x1 = 3 + index * (segment_width + gap)
                x2 = x1 + segment_width
                y1, y2 = 4, height - 4

            threshold = (index + 1) / self.segments
            active = self._display_level >= threshold
            fill = self._segment_color(index) if active else "#11171B"
            outline = fill if active else "#202A2F"
            self.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline, width=1)

        peak_index = min(self.segments - 1, max(0, int(self._peak_level * self.segments)))
        if self._peak_level > 0.04:
            if self.orientation == "vertical":
                segment_height = (height - gap * (self.segments - 1)) / self.segments
                y2 = height - peak_index * (segment_height + gap)
                y1 = y2 - segment_height
                self.create_rectangle(2, y1, width - 2, y1 + 2, fill=self.palette.text, outline="")
            else:
                segment_width = (width - 6 - gap * (self.segments - 1)) / self.segments
                x1 = 3 + peak_index * (segment_width + gap)
                self.create_rectangle(x1, 2, x1 + 2, height - 2, fill=self.palette.text, outline="")

    def _segment_color(self, index: int) -> str:
        ratio = index / max(1, self.segments - 1)
        if ratio < 0.45:
            return self.palette.meter_low
        if ratio < 0.75:
            return self.palette.meter_mid
        return self.palette.meter_high


class StudioFader(tk.Canvas):
    def __init__(
        self,
        master,
        *,
        variable: tk.IntVar,
        from_: int = 150,
        to: int = 0,
        width: int = 58,
        height: int = 210,
        palette: MixerPalette = PALETTE,
        command=None,
        select_command=None,
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            bg=palette.panel_alt,
            bd=0,
            highlightthickness=0,
            cursor="sb_v_double_arrow",
            **kwargs,
        )
        self.variable = variable
        self.from_ = from_
        self.to = to
        self.palette = palette
        self.command = command
        self.select_command = select_command
        self._dragging = False
        self._trace_id = self.variable.trace_add("write", lambda *_args: self.redraw())
        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.redraw()

    def destroy(self) -> None:
        try:
            self.variable.trace_remove("write", self._trace_id)
        except tk.TclError:
            pass
        super().destroy()

    def redraw(self) -> None:
        self.delete("all")
        width = max(40, int(self.winfo_width() or self["width"]))
        height = max(120, int(self.winfo_height() or self["height"]))
        top = 12
        bottom = height - 18
        center = width // 2
        track_left = center - 5
        track_right = center + 5
        knob_y = self._value_to_y(self.variable.get(), top, bottom)

        self.create_rectangle(0, 0, width, height, fill=self.palette.panel_alt, outline="")
        self.create_rectangle(track_left - 3, top, track_right + 3, bottom, fill="#0A0F11", outline="#263238")
        self.create_rectangle(track_left, knob_y, track_right, bottom, fill=self.palette.accent, outline="")

        for value in (0, 25, 50, 75, 100, 125, 150):
            y = self._value_to_y(value, top, bottom)
            tick_width = 15 if value in (0, 100, 150) else 9
            self.create_line(center - tick_width, y, center - 8, y, fill=self.palette.text_dim)
            self.create_line(center + 8, y, center + tick_width, y, fill=self.palette.text_dim)
            if value in (0, 100, 150):
                self.create_text(center + 22, y, text=str(value), fill=self.palette.text_dim, font=("DejaVu Sans Condensed", 7))

        knob_center = max(18, center - 9)
        knob_half_width = min(17, max(13, width // 4))
        knob_height = 18
        x1 = knob_center - knob_half_width
        x2 = knob_center + knob_half_width
        y1 = knob_y - knob_height // 2
        y2 = knob_y + knob_height // 2
        self.create_rectangle(x1 + 2, y1 + 2, x2 + 2, y2 + 2, fill="#050708", outline="")
        self.create_rectangle(x1, y1, x2, y2, fill="#D2CDC1", outline="#F6F1E8", width=1)
        self.create_line(x1 + 5, knob_y, x2 - 5, knob_y, fill="#6B6F6A", width=2)
        self.create_line(x1 + 6, y1 + 4, x2 - 6, y1 + 4, fill="#FFFFFF")

    def _value_to_y(self, value: float, top: int, bottom: int) -> float:
        high = max(self.from_, self.to)
        low = min(self.from_, self.to)
        clamped = max(low, min(high, float(value)))
        ratio = (high - clamped) / max(1, high - low)
        return top + ratio * (bottom - top)

    def _y_to_value(self, y: float) -> int:
        height = max(120, int(self.winfo_height() or self["height"]))
        top = 12
        bottom = height - 18
        ratio = (y - top) / max(1, bottom - top)
        high = max(self.from_, self.to)
        low = min(self.from_, self.to)
        value = high - ratio * (high - low)
        return int(round(max(low, min(high, value))))

    def _set_from_event(self, event) -> None:
        self.variable.set(self._y_to_value(event.y))
        if self.command is not None:
            self.command()

    def _on_press(self, event) -> None:
        self._dragging = True
        if self.select_command is not None:
            self.select_command()
        self._set_from_event(event)

    def _on_drag(self, event) -> None:
        if self._dragging:
            self._set_from_event(event)

    def _on_release(self, _event) -> None:
        self._dragging = False
