"""Configuration service for MeshCore-TUI."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable
import shutil

import yaml

CONFIG_PATH = Path("config/config.yaml")
CONFIG_EXAMPLE_PATH = Path("config/config.example.yaml")


def _load_defaults(cls, data: dict[str, Any] | None) -> Any:
    """Helper to merge provided dict data with dataclass defaults."""
    if data is None:
        return cls()
    defaults = cls()
    values = {}
    for field_name in defaults.__dataclass_fields__:  # type: ignore[attr-defined]
        values[field_name] = data.get(field_name, getattr(defaults, field_name))
    return cls(**values)


@dataclass
class CompanionConnectionConfig:
    transport: str = "bluetooth"
    endpoint: str = "meshcore-dev.local"
    device: str = "auto"
    channel_refresh_seconds: int = 30

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CompanionConnectionConfig":
        return _load_defaults(cls, data)


@dataclass
class MeshcoreConfig:
    companion: CompanionConnectionConfig = field(default_factory=CompanionConnectionConfig)
    log_packets: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MeshcoreConfig":
        if data is None:
            return cls()
        return cls(
            companion=CompanionConnectionConfig.from_dict(data.get("companion")),
            log_packets=data.get("log_packets", False),
        )


@dataclass
class UIConfig:
    theme: str = "meshcore-dark"
    log_level: str = "info"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "UIConfig":
        return _load_defaults(cls, data)


@dataclass
class AppConfig:
    version: int = 1
    meshcore: MeshcoreConfig = field(default_factory=MeshcoreConfig)
    ui: UIConfig = field(default_factory=UIConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AppConfig":
        if data is None:
            return cls()
        return cls(
            version=data.get("version", 1),
            meshcore=MeshcoreConfig.from_dict(data.get("meshcore")),
            ui=UIConfig.from_dict(data.get("ui")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConfigService:
    """Loads and persists application configuration to config/config.yaml."""

    def __init__(self, path: Path | str | None = None, example_path: Path | str | None = None) -> None:
        self.path = Path(path or CONFIG_PATH)
        self.example_path = Path(example_path or CONFIG_EXAMPLE_PATH)
        self._config = self._load()

    @property
    def config(self) -> AppConfig:
        return self._config

    def reload(self) -> AppConfig:
        self._config = self._load()
        return self._config

    def mutate(self, fn: Callable[[AppConfig], None]) -> AppConfig:
        """Apply a mutation function to the config and persist it."""
        fn(self._config)
        self.save()
        return self._config

    def save(self, config: AppConfig | None = None) -> None:
        if config is not None:
            self._config = config
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(self._config.to_dict(), handle, sort_keys=False)

    def _load(self) -> AppConfig:
        data = self._read_yaml()
        return AppConfig.from_dict(data)

    def _read_yaml(self) -> dict[str, Any] | None:
        self._ensure_file()
        with self.path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return data

    def _ensure_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            return
        if self.example_path.exists():
            shutil.copy(self.example_path, self.path)
        else:
            with self.path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(AppConfig().to_dict(), handle, sort_keys=False)
