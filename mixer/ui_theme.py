from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk
from tkinter import font, ttk


@dataclass(frozen=True)
class MixerPalette:
    bg: str = "#090B16"
    bg_alt: str = "#0F1324"
    panel: str = "#11172A"
    panel_alt: str = "#171E35"
    panel_selected: str = "#1D2644"
    border: str = "#253255"
    border_glow: str = "#3D7CFF"
    text: str = "#F1F5FF"
    text_muted: str = "#8FA3CC"
    text_dim: str = "#66789D"
    accent: str = "#56A7FF"
    accent_soft: str = "#7F6BFF"
    accent_bright: str = "#82F3FF"
    success: str = "#1DD6A2"
    warning: str = "#FFBC52"
    danger: str = "#FF6D8B"
    meter_low: str = "#1DD6A2"
    meter_mid: str = "#6CD8FF"
    meter_high: str = "#ff7cc4"


PALETTE = MixerPalette()


def apply_theme(root: tk.Misc) -> ttk.Style:
    style = ttk.Style(root)
    style.theme_use("clam")

    default_font = font.nametofont("TkDefaultFont")
    default_font.configure(family="DejaVu Sans", size=10)
    text_font = font.nametofont("TkTextFont")
    text_font.configure(family="DejaVu Sans", size=10)
    heading_font = font.Font(root=root, family="DejaVu Sans", size=20, weight="bold")
    section_font = font.Font(root=root, family="DejaVu Sans", size=11, weight="bold")
    compact_font = font.Font(root=root, family="DejaVu Sans", size=9)

    root.option_add("*Font", default_font)
    root.option_add("*TCombobox*Listbox.background", PALETTE.panel_alt)
    root.option_add("*TCombobox*Listbox.foreground", PALETTE.text)
    root.option_add("*TCombobox*Listbox.selectBackground", PALETTE.accent_soft)
    root.option_add("*TCombobox*Listbox.selectForeground", PALETTE.text)
    root.option_add("*Menu.background", PALETTE.panel_alt)
    root.option_add("*Menu.foreground", PALETTE.text)

    root.configure(bg=PALETTE.bg)

    style.configure(".", background=PALETTE.bg, foreground=PALETTE.text, fieldbackground=PALETTE.panel_alt)

    style.configure("Shell.TFrame", background=PALETTE.bg)
    style.configure("Surface.TFrame", background=PALETTE.bg_alt)
    style.configure("Panel.TFrame", background=PALETTE.panel, relief="flat", borderwidth=0)
    style.configure("Card.TFrame", background=PALETTE.panel_alt, relief="flat", borderwidth=1)
    style.configure("SelectedCard.TFrame", background=PALETTE.panel_selected, relief="flat", borderwidth=1)
    style.configure("Toolbar.TFrame", background=PALETTE.bg)
    style.configure("Status.TFrame", background=PALETTE.panel)

    style.configure("Title.TLabel", background=PALETTE.bg, foreground=PALETTE.text, font=heading_font)
    style.configure("Subtitle.TLabel", background=PALETTE.bg, foreground=PALETTE.text_muted, font=compact_font)
    style.configure("Section.TLabel", background=PALETTE.panel, foreground=PALETTE.text, font=section_font)
    style.configure("CardTitle.TLabel", background=PALETTE.panel_alt, foreground=PALETTE.text, font=section_font)
    style.configure("CardSubtitle.TLabel", background=PALETTE.panel_alt, foreground=PALETTE.text_muted, font=compact_font)
    style.configure("Body.TLabel", background=PALETTE.panel, foreground=PALETTE.text)
    style.configure("Muted.TLabel", background=PALETTE.panel, foreground=PALETTE.text_muted)
    style.configure("Status.TLabel", background=PALETTE.panel, foreground=PALETTE.text)
    style.configure("Badge.TLabel", background=PALETTE.panel_alt, foreground=PALETTE.accent_bright, font=compact_font)

    style.configure(
        "Panel.TLabelframe",
        background=PALETTE.panel,
        foreground=PALETTE.text,
        bordercolor=PALETTE.border,
        relief="solid",
        borderwidth=1,
        lightcolor=PALETTE.border,
        darkcolor=PALETTE.border,
    )
    style.configure("Panel.TLabelframe.Label", background=PALETTE.panel, foreground=PALETTE.text, font=section_font)

    style.configure(
        "TButton",
        background=PALETTE.panel_alt,
        foreground=PALETTE.text,
        bordercolor=PALETTE.border,
        lightcolor=PALETTE.panel_alt,
        darkcolor=PALETTE.panel_alt,
        relief="flat",
        padding=(14, 9),
    )
    style.map(
        "TButton",
        background=[("active", PALETTE.panel_selected), ("pressed", PALETTE.panel_selected)],
        foreground=[("disabled", PALETTE.text_dim)],
    )

    style.configure(
        "Accent.TButton",
        background=PALETTE.accent,
        foreground="#06111F",
        bordercolor=PALETTE.accent_bright,
        lightcolor=PALETTE.accent,
        darkcolor=PALETTE.accent,
    )
    style.map(
        "Accent.TButton",
        background=[("active", PALETTE.accent_bright), ("pressed", PALETTE.accent_soft)],
        foreground=[("pressed", "#06111F")],
    )

    style.configure(
        "Ghost.TButton",
        background=PALETTE.panel,
        foreground=PALETTE.text_muted,
        bordercolor=PALETTE.border,
    )
    style.map("Ghost.TButton", background=[("active", PALETTE.panel_alt)], foreground=[("active", PALETTE.text)])

    style.configure(
        "TEntry",
        fieldbackground=PALETTE.panel,
        foreground=PALETTE.text,
        insertcolor=PALETTE.accent_bright,
        bordercolor=PALETTE.border,
        lightcolor=PALETTE.border,
        darkcolor=PALETTE.border,
        padding=8,
    )
    style.configure(
        "TCombobox",
        fieldbackground=PALETTE.panel,
        foreground=PALETTE.text,
        arrowcolor=PALETTE.accent_bright,
        bordercolor=PALETTE.border,
        lightcolor=PALETTE.border,
        darkcolor=PALETTE.border,
        padding=6,
    )
    style.map("TCombobox", fieldbackground=[("readonly", PALETTE.panel)], background=[("readonly", PALETTE.panel)])

    style.configure(
        "TSpinbox",
        fieldbackground=PALETTE.panel,
        foreground=PALETTE.text,
        arrowcolor=PALETTE.accent_bright,
        bordercolor=PALETTE.border,
        lightcolor=PALETTE.border,
        darkcolor=PALETTE.border,
        padding=6,
    )

    style.configure(
        "Mixer.Horizontal.TScale",
        background=PALETTE.panel_alt,
        troughcolor=PALETTE.bg_alt,
        lightcolor=PALETTE.accent,
        darkcolor=PALETTE.accent_soft,
        bordercolor=PALETTE.bg_alt,
    )
    style.configure(
        "Mixer.Vertical.TScale",
        background=PALETTE.panel_alt,
        troughcolor=PALETTE.bg_alt,
        lightcolor=PALETTE.accent,
        darkcolor=PALETTE.accent_soft,
        bordercolor=PALETTE.bg_alt,
    )
    style.map(
        "Mixer.Vertical.TScale",
        background=[("active", PALETTE.panel_alt)],
        troughcolor=[("active", PALETTE.bg_alt)],
    )

    return style
