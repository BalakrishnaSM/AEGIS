import os, sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
DATASET = os.environ.get("AEGIS_DATASET", "/tmp/aegis/task-data/aegis-dataset/aegis-dataset")

@pytest.fixture(scope="session")
def kb():
    if not Path(DATASET).exists(): pytest.skip("dataset not available (set AEGIS_DATASET)")
    from aegis.ingest.pipeline import ingest
    k, _ = ingest(DATASET, None, str(Path(__file__).resolve().parents[1] / "data/transcriptions")); return k

@pytest.fixture(scope="session")
def engine(kb):
    from aegis.query.pipeline import Engine
    return Engine(kb)
