"""Read frozen public inputs without depending on daily production snapshots."""
import json
from pathlib import Path


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "pipeline_offline"


def load_fixture(name):
    with (FIXTURE_DIR / name).open(encoding="utf-8") as stream:
        return json.load(stream)
