"""Strict JSON input and atomic local writes for the reconstructed prototype."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Nombre JSON non fini interdit : {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nombre JSON hors de la plage numérique prise en charge.")
    return number


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Clé JSON répétée : {key}")
        result[key] = value
    return result


def load_json(path: str | Path) -> Any:
    """Load strict UTF-8 JSON: no duplicate keys, NaN, or Infinity."""
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream, parse_constant=_invalid_constant, parse_float=_finite_float,
                         object_pairs_hook=_unique_object)


def atomic_write(path: str | Path, text: str) -> Path:
    """Write a complete sibling temporary file, then replace the destination."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n",
                                         dir=destination.parent,
                                         prefix=f".{destination.name}.",
                                         suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return destination


def write_json(path: str | Path, data: Any) -> Path:
    return atomic_write(path, json.dumps(data, ensure_ascii=False, allow_nan=False,
                                        indent=2, sort_keys=True) + "\n")


def positive_number(value: Any, label: str = "target_size") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} doit être un nombre strictement positif.")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} doit être fini et strictement positif.")
    return result


def normalize_case(data: Any, *, size_override: float | None = None,
                   default_name: str = "piece") -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Chaque pièce doit être un objet JSON.")
    if data.get("holes"):
        raise ValueError("Les contours avec trous ne sont pas pris en charge dans cette version.")
    if "points" not in data:
        raise ValueError("La pièce doit contenir une liste 'points'.")
    points = data["points"]
    if (not isinstance(points, list) or not points
            or any(not isinstance(point, (list, tuple)) or len(point) != 2
                   or any(isinstance(value, bool) or not isinstance(value, (int, float))
                          or not math.isfinite(value) for value in point)
                   for point in points)):
        raise ValueError("points doit contenir des coordonnées JSON [x,y] finies")
    # File retrieval preserves the supplied order. Polygon orientation,
    # topology and geometric validity belong to the native kernel.
    points = [tuple(point) for point in points]
    size = positive_number(size_override if size_override is not None else data.get("target_size"))
    name = data.get("name", default_name)
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Le nom de la pièce doit être une chaîne non vide.")
    result: dict[str, Any] = {"name": name, "points": points, "target_size": size}
    if "family" in data:
        if not isinstance(data["family"], str) or not data["family"].strip():
            raise ValueError("La famille doit être une chaîne non vide.")
        result["family"] = data["family"]
    return result


def load_case(path: str | Path, *, size_override: float | None = None) -> dict[str, Any]:
    return normalize_case(load_json(path), size_override=size_override, default_name=Path(path).stem)


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    data = load_json(path)
    if isinstance(data, dict) and "cases" in data:
        data = data["cases"]
    if not isinstance(data, list) or not data:
        raise ValueError("Le corpus doit être une liste JSON non vide de pièces.")
    return [normalize_case(row, default_name=f"piece_{index + 1}") for index, row in enumerate(data)]
