import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .store import Store


@dataclass(frozen=True)
class Settings:
    dataset: Path
    database: Path

    @classmethod
    def load(cls):
        load_dotenv(Path.cwd() / ".env.local", override=False)
        return cls(
            Path(os.getenv("FLEET_DATASET", "data/raw/device-telemetry-dataset.ndjson")),
            Path(os.getenv("FLEET_DB", "runtime/fleet.sqlite3")),
        )

    def store(self):
        store = Store(self.database)
        store.ingest(self.dataset)
        return store
