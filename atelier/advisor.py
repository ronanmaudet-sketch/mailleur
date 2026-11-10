"""Transport-only compatibility facade for the Rust strategy guide.

The legacy network is distinct from the continuous/corpus models. Its feature
calculation, fitting, validation and inference are all performed by Rust.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

from mailleur.io import load_json

ALGORITHMS = ("automatic", "delaunay", "frontal")
FEATURES = ("mode", "thin_extent", "middle_extent", "relative_size", "hole_fraction",
            "curved", "relative_corner_size", "boundary_complexity")
VERSION = 1
MIN_IMPROVEMENT = 1e-4


def _query(operation, **payload):
    from .native import NativeSession
    with tempfile.TemporaryDirectory(prefix="atelier-advice-") as folder:
        return NativeSession(Path(folder)).query(operation, **payload)


def extract_features(project):
    return _query("advisor_features", project=project)["features"]


def default_cases():
    return _query("advisor_default_cases")["cases"]


def choose_algorithm(project, model_path):
    path = Path(model_path)
    model = load_json(path) if path.is_file() else None
    return _query("advisor_choose", project=project, model=model)


def train_advisor(workdir: Path, cases=None, epochs=200, seed=42):
    from .native import NativeSession
    directory = Path(workdir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    result = NativeSession(directory / "native_runtime").train(cases=cases, epochs=epochs, seed=seed)
    for name in ("report.json", "guide.json"):
        shutil.copyfile(Path(result["directory"]) / name, directory / name)
    return result["report"]


def _retired(*args, **kwargs):
    raise RuntimeError("L'API interne d'entraînement Python a été retirée ; utiliser les opérations du noyau Rust.")


_normalise_cases = _split = _collect = _forward = _fit = _validate_model = _retired
