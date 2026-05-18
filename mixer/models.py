from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SinkInfo:
    name: str
    description: str


@dataclass
class SourceInfo:
    name: str
    description: str
    channels: int = 0
    channel_map: str = ""


@dataclass
class VirtualDeviceSpec:
    key: str
    sink_name: str
    description: str
    source_name: str = ""
    source_description: str = ""


@dataclass
class RouteTargetSelection:
    a1_sink: str = ""
    a2_sink: str = ""


@dataclass
class RoutingMatrix:
    routes: dict[str, dict[str, bool]] = field(default_factory=dict)

    def enabled_pairs(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for source_key, targets in self.routes.items():
            for target_key, enabled in targets.items():
                if enabled:
                    pairs.append((source_key, target_key))
        return pairs
