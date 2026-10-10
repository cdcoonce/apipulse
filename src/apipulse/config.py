import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path


def resolve_settings(data_dir: Path | None = None) -> Settings:
    if data_dir is None:
        env = os.environ.get("APIPULSE_DATA_DIR")
        data_dir = Path(env) if env else Path("data")
    return Settings(data_dir=data_dir)
