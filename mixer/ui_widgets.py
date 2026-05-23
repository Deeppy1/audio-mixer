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
            font=("DejaVu Sans", 9, "bold"),
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
        self._draw_meter()
        self.after(90, self._tick)

    def _draw_meter(self) -> None:
        self.delete("all")
        width = max(1, int(self.winfo_width() or self["width"]))
        height = max(1, int(self.winfo_height() or self["height"]))
        gap = 2 if self.orientation == "vertical" else 1

        for index in range(self.segments):
            if self.orientation == "vertical":
                segment_height = (height - gap * (self.segments - 1)) / self.segments
                y2 = height - index * (segment_height + gap)
                y1 = y2 - segment_height
                x1, x2 = 3, width - 3
            else:
                segment_width = (width - gap * (self.segments - 1)) / self.segments
                x1 = index * (segment_width + gap)
                x2 = x1 + segment_width
                y1, y2 = 3, height - 3

            threshold = (index + 1) / self.segments
            active = self._display_level >= threshold
            fill = self._segment_color(index) if active else self.palette.panel
            outline = fill if active else self.palette.border
            self.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline, width=1)

    def _segment_color(self, index: int) -> str:
        ratio = index / max(1, self.segments - 1)
        if ratio < 0.45:
            return self.palette.meter_low
        if ratio < 0.75:
            return self.palette.meter_mid
        return self.palette.meter_high
