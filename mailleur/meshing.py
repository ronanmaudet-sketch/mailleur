"""Legacy mesh DTO and Rust analysis transport, without Python mesh algorithms.

Ear clipping, subdivision, smoothing and Python quality calculations have been
removed. The DTO remains usable for JSON/MSH/SVG file exchange.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tempfile

from .geometry import LegacyNumericalAPIError, Point

STRATEGIES = ("quality", "shortest", "balanced", "first")


@dataclass
class Mesh:
    points: list[Point]
    quads: list[tuple[int, int, int, int]]
    boundary: list[tuple[int, int]]
    metadata: dict = field(default_factory=dict)


def generate_mesh(*args, **kwargs):
    raise LegacyNumericalAPIError(
        "Le mailleur Python du prototype a été retiré en rc4. "
        "Utiliser NativeSession.generate avec geometry.kind='polygon' "
        "ou la commande atelier-mailleur mesh pour le noyau Rust."
    )


def _metrics(mesh: Mesh):
    """Serialize the DTO; the native kernel performs every metric calculation."""
    from atelier.native import NativeSession
    preview = {
        "points": [[x, y, 0] for x, y in mesh.points],
        "cells": [{"type": "quad", "data": mesh.quads},
                  {"type": "line", "data": mesh.boundary}],
    }
    with tempfile.TemporaryDirectory(prefix="atelier-mesh-metrics-") as folder:
        try:
            return NativeSession(Path(folder)).query("metrics", preview=preview)
        except RuntimeError as error:
            raise ValueError(str(error)) from error


def _summary(metrics):
    quality = metrics["top_dimension_quality"]
    return {
        "vertices": metrics["points"], "elements": metrics["top_dimension_elements"],
        "num_quads": metrics["cell_counts"].get("quad", 0),
        "boundary_edges": metrics["topology"].get("open_surface_edges"),
        "min_scaled_jacobian": quality["minimum"],
        "mean_scaled_jacobian": quality["mean"],
        "max_edge": metrics["edges"]["maximum"],
        "area": metrics["stored_surface_area"],
        "computation_backend": "rust",
        "validation_scope": "primary-corner geometry and incidence; declared boundary membership is not certified",
    }


def quality_summary(mesh: Mesh) -> dict:
    return _summary(_metrics(mesh))


def validate_mesh(mesh: Mesh, polygon=None) -> dict:
    if polygon is not None:
        raise LegacyNumericalAPIError(
            "La validation du prototype par rapport à un contour source a été retirée. "
            "Importer ce contour dans NativeSession pour construire et contrôler le domaine."
        )
    metrics = _metrics(mesh)
    topology = metrics["topology"]
    if (metrics["top_dimension"] != 2 or not metrics["top_dimension_elements"]
            or metrics["top_dimension_quality"]["minimum"] <= 0
            or topology["duplicate_cells"] or topology["nonmanifold_surface_edges"]
            or metrics["unused_preview_points"]):
        raise ValueError("Le contrôle Rust a refusé la connectivité ou la qualité du maillage")
    return {**_summary(metrics), "valid": True}
