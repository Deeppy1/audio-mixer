from __future__ import annotations

import math
from dataclasses import dataclass, field


MIN_FREQUENCY = 20.0
MAX_FREQUENCY = 20000.0
MIN_GAIN_DB = -18.0
MAX_GAIN_DB = 18.0
MIN_Q = 0.2
MAX_Q = 8.0
GRAPH_FREQUENCIES = [
    MIN_FREQUENCY * pow(MAX_FREQUENCY / MIN_FREQUENCY, index / 255.0)
    for index in range(256)
]


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def freq_to_normalized(frequency: float) -> float:
    frequency = clamp(frequency, MIN_FREQUENCY, MAX_FREQUENCY)
    return math.log10(frequency / MIN_FREQUENCY) / math.log10(MAX_FREQUENCY / MIN_FREQUENCY)


def normalized_to_freq(value: float) -> float:
    value = clamp(value, 0.0, 1.0)
    return MIN_FREQUENCY * pow(MAX_FREQUENCY / MIN_FREQUENCY, value)


@dataclass
class EQBand:
    label: str
    kind: str
    frequency: float
    gain_db: float = 0.0
    q: float = 1.0
    enabled: bool = True

    def to_dict(self) -> dict[str, float | str | bool]:
        return {
            "label": self.label,
            "kind": self.kind,
            "frequency": round(clamp(self.frequency, MIN_FREQUENCY, MAX_FREQUENCY), 3),
            "gain_db": round(clamp(self.gain_db, MIN_GAIN_DB, MAX_GAIN_DB), 3),
            "q": round(clamp(self.q, MIN_Q, MAX_Q), 3),
            "enabled": bool(self.enabled),
        }

    @classmethod
    def from_dict(cls, payload: dict, fallback: "EQBand") -> "EQBand":
        return cls(
            label=str(payload.get("label", fallback.label)),
            kind=str(payload.get("kind", fallback.kind)),
            frequency=clamp(float(payload.get("frequency", fallback.frequency)), MIN_FREQUENCY, MAX_FREQUENCY),
            gain_db=clamp(float(payload.get("gain_db", fallback.gain_db)), MIN_GAIN_DB, MAX_GAIN_DB),
            q=clamp(float(payload.get("q", fallback.q)), MIN_Q, MAX_Q),
            enabled=bool(payload.get("enabled", fallback.enabled)),
        )


@dataclass
class EQState:
    preset_name: str = "Flat"
    bypass: bool = False
    bands: list[EQBand] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "preset_name": self.preset_name,
            "bypass": bool(self.bypass),
            "bands": [band.to_dict() for band in self.bands],
        }

    @classmethod
    def default(cls) -> "EQState":
        return cls(
            preset_name="Flat",
            bypass=False,
            bands=[
                EQBand("Low", "lowshelf", 80.0, 0.0, 0.7, True),
                EQBand("Low Mid", "peak", 250.0, 0.0, 1.0, True),
                EQBand("Mid", "peak", 1000.0, 0.0, 1.0, True),
                EQBand("High Mid", "peak", 4000.0, 0.0, 1.0, True),
                EQBand("High", "highshelf", 12000.0, 0.0, 0.7, True),
            ],
        )

    @classmethod
    def from_dict(cls, payload: dict | None) -> "EQState":
        default = cls.default()
        if not payload:
            return default

        payload_bands = payload.get("bands", [])
        bands: list[EQBand] = []
        for index, fallback in enumerate(default.bands):
            source = payload_bands[index] if isinstance(payload_bands, list) and index < len(payload_bands) else {}
            try:
                bands.append(EQBand.from_dict(source, fallback))
            except (TypeError, ValueError):
                bands.append(fallback)
        state = cls(
            preset_name=str(payload.get("preset_name", default.preset_name)),
            bypass=bool(payload.get("bypass", default.bypass)),
            bands=bands,
        )
        state.normalize_band_order()
        return state

    def copy(self) -> "EQState":
        return EQState.from_dict(self.to_dict())

    def normalize_band_order(self) -> None:
        for index, band in enumerate(self.bands):
            minimum = MIN_FREQUENCY if index == 0 else self.bands[index - 1].frequency * 1.1
            maximum = MAX_FREQUENCY if index == len(self.bands) - 1 else self.bands[index + 1].frequency / 1.1
            band.frequency = clamp(band.frequency, minimum, maximum)
            band.gain_db = clamp(band.gain_db, MIN_GAIN_DB, MAX_GAIN_DB)
            band.q = clamp(band.q, MIN_Q, MAX_Q)


def band_magnitude_db(band: EQBand, frequency: float, sample_rate: float = 48000.0) -> float:
    if not band.enabled or abs(band.gain_db) < 0.001:
        return 0.0

    omega = 2.0 * math.pi * clamp(frequency, MIN_FREQUENCY, sample_rate * 0.495) / sample_rate
    omega0 = 2.0 * math.pi * clamp(band.frequency, MIN_FREQUENCY, sample_rate * 0.495) / sample_rate
    cos_omega0 = math.cos(omega0)
    sin_omega0 = math.sin(omega0)
    q = clamp(band.q, MIN_Q, MAX_Q)
    a = pow(10.0, band.gain_db / 40.0)

    if band.kind == "peak":
        alpha = sin_omega0 / (2.0 * q)
        b0 = 1.0 + alpha * a
        b1 = -2.0 * cos_omega0
        b2 = 1.0 - alpha * a
        a0 = 1.0 + alpha / a
        a1 = -2.0 * cos_omega0
        a2 = 1.0 - alpha / a
    else:
        shelf_s = max(0.25, min(2.0, 1.0 / q))
        alpha = sin_omega0 / 2.0 * math.sqrt((a + 1.0 / a) * (1.0 / shelf_s - 1.0) + 2.0)
        beta = 2.0 * math.sqrt(a) * alpha
        if band.kind == "lowshelf":
            b0 = a * ((a + 1.0) - (a - 1.0) * cos_omega0 + beta)
            b1 = 2.0 * a * ((a - 1.0) - (a + 1.0) * cos_omega0)
            b2 = a * ((a + 1.0) - (a - 1.0) * cos_omega0 - beta)
            a0 = (a + 1.0) + (a - 1.0) * cos_omega0 + beta
            a1 = -2.0 * ((a - 1.0) + (a + 1.0) * cos_omega0)
            a2 = (a + 1.0) + (a - 1.0) * cos_omega0 - beta
        else:
            b0 = a * ((a + 1.0) + (a - 1.0) * cos_omega0 + beta)
            b1 = -2.0 * a * ((a - 1.0) + (a + 1.0) * cos_omega0)
            b2 = a * ((a + 1.0) + (a - 1.0) * cos_omega0 - beta)
            a0 = (a + 1.0) - (a - 1.0) * cos_omega0 + beta
            a1 = 2.0 * ((a - 1.0) - (a + 1.0) * cos_omega0)
            a2 = (a + 1.0) - (a - 1.0) * cos_omega0 - beta

    cos_omega = math.cos(omega)
    cos_2omega = math.cos(2.0 * omega)
    sin_omega = math.sin(omega)
    sin_2omega = math.sin(2.0 * omega)

    numerator_real = b0 + b1 * cos_omega + b2 * cos_2omega
    numerator_imag = -(b1 * sin_omega + b2 * sin_2omega)
    denominator_real = a0 + a1 * cos_omega + a2 * cos_2omega
    denominator_imag = -(a1 * sin_omega + a2 * sin_2omega)

    numerator = math.sqrt(numerator_real * numerator_real + numerator_imag * numerator_imag)
    denominator = math.sqrt(denominator_real * denominator_real + denominator_imag * denominator_imag)
    if denominator <= 0.0 or numerator <= 0.0:
        return 0.0
    return 20.0 * math.log10(numerator / denominator)


def build_response_curve(state: EQState, frequencies: list[float] | None = None) -> list[tuple[float, float]]:
    if frequencies is None:
        frequencies = GRAPH_FREQUENCIES
    if state.bypass:
        return [(frequency, 0.0) for frequency in frequencies]

    response: list[tuple[float, float]] = []
    for frequency in frequencies:
        gain_db = 0.0
        for band in state.bands:
            gain_db += band_magnitude_db(band, frequency)
        response.append((frequency, clamp(gain_db, MIN_GAIN_DB * 1.6, MAX_GAIN_DB * 1.6)))
    return response


def default_presets() -> dict[str, EQState]:
    presets = {
        "Flat": EQState.default(),
        "Vocal Presence": EQState(
            preset_name="Vocal Presence",
            bands=[
                EQBand("Low", "lowshelf", 95.0, -2.0, 0.8, True),
                EQBand("Low Mid", "peak", 280.0, -1.5, 1.1, True),
                EQBand("Mid", "peak", 2200.0, 2.7, 1.2, True),
                EQBand("High Mid", "peak", 5400.0, 1.8, 1.0, True),
                EQBand("High", "highshelf", 12000.0, 2.0, 0.8, True),
            ],
        ),
        "Bass Focus": EQState(
            preset_name="Bass Focus",
            bands=[
                EQBand("Low", "lowshelf", 70.0, 4.5, 0.7, True),
                EQBand("Low Mid", "peak", 220.0, 1.5, 0.9, True),
                EQBand("Mid", "peak", 900.0, -1.8, 1.0, True),
                EQBand("High Mid", "peak", 3500.0, 0.8, 1.1, True),
                EQBand("High", "highshelf", 10500.0, 1.2, 0.7, True),
            ],
        ),
        "Broadcast": EQState(
            preset_name="Broadcast",
            bands=[
                EQBand("Low", "lowshelf", 110.0, -1.5, 0.8, True),
                EQBand("Low Mid", "peak", 250.0, -2.2, 1.3, True),
                EQBand("Mid", "peak", 1800.0, 1.8, 1.0, True),
                EQBand("High Mid", "peak", 4200.0, 2.0, 1.0, True),
                EQBand("High", "highshelf", 10000.0, -0.5, 0.8, True),
            ],
        ),
        "Airy Master": EQState(
            preset_name="Airy Master",
            bands=[
                EQBand("Low", "lowshelf", 85.0, 1.8, 0.7, True),
                EQBand("Low Mid", "peak", 320.0, -1.0, 1.0, True),
                EQBand("Mid", "peak", 2400.0, 1.2, 0.8, True),
                EQBand("High Mid", "peak", 7000.0, 2.1, 0.9, True),
                EQBand("High", "highshelf", 14500.0, 3.0, 0.7, True),
            ],
        ),
    }
    for preset in presets.values():
        preset.normalize_band_order()
    return presets
