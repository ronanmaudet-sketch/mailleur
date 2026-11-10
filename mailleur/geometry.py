"""Compatibility names for the retired Python numerical prototype.

Geometry is prepared and validated by the Rust kernel. This module keeps
imports explicit instead of retaining an executable Python geometry engine.
"""
from __future__ import annotations

Point = tuple[float, float]
EPS = 2e-14  # Historical public constant, without a numerical implementation.


class LegacyNumericalAPIError(RuntimeError):
    """The removed Python numerical API cannot be executed."""


def _retired(*args, **kwargs):
    raise LegacyNumericalAPIError(
        "L'ancien noyau numérique Python a été retiré en rc4. "
        "Utiliser atelier.native.NativeSession et un projet déclaratif : "
        "la géométrie, le maillage et leurs calculs sont exécutés dans Rust."
    )


cross = polygon_area = local_frame = on_segment = segments_intersect = _retired
prepare_polygon = geometry_id = _retired
