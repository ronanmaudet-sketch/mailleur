"""Mesh DTO JSON, standalone SVG and Gmsh MSH 2.2 ASCII file exports.

Numerical mesh checks and quality measurements are requested from Rust. Python
only validates the file schema, formats output and lays out the SVG drawing.

MSH reference: https://www.gmsh.info/doc/texinfo/gmsh.html#MSH-file-format-version-2-_0028Legacy_0029
Boundary segments are type 1 with physical tag 1; quadrilaterals are type 3,
physical tag 2. Node indices in MSH are one-based; JSON uses zero-based indices.
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any

from .io import atomic_write, load_json
from .meshing import Mesh, quality_summary, validate_mesh

SCHEMA = "mailleur.mesh.v1"


def mesh_document(mesh: Mesh) -> dict[str, Any]:
    validate_mesh(mesh)
    return {"schema": SCHEMA, "dimension": 2, "index_base": 0,
            "points": mesh.points, "quads": mesh.quads,
            "boundary": mesh.boundary, "metadata": mesh.metadata,
            "quality": quality_summary(mesh)}


def read_mesh(path: str | Path) -> Mesh:
    data = load_json(path)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(f"Format de maillage attendu : {SCHEMA}")
    if data.get("dimension") != 2 or data.get("index_base") != 0:
        raise ValueError("Seuls les maillages 2D à indices commençant à 0 sont acceptés.")
    points = data.get("points")
    if not isinstance(points, list) or not points:
        raise ValueError("Liste de points absente ou vide.")
    converted = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Chaque sommet doit avoir deux coordonnées.")
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in point):
            raise ValueError("Coordonnées non numériques ou non finies.")
        converted.append((float(point[0]), float(point[1])))

    def connectivity(name: str, size: int) -> list[tuple[int, ...]]:
        rows = data.get(name)
        if not isinstance(rows, list):
            raise ValueError(f"Liste '{name}' absente.")
        result = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != size:
                raise ValueError(f"Connectivité '{name}' incorrecte.")
            if any(isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(converted) for index in row):
                raise ValueError(f"Indice invalide dans '{name}'.")
            result.append(tuple(row))
        return result

    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Les métadonnées doivent être un objet JSON.")
    mesh = Mesh(points=converted, quads=connectivity("quads", 4),
                boundary=connectivity("boundary", 2), metadata=metadata)
    validate_mesh(mesh)
    return mesh


def gmsh_text(mesh: Mesh) -> str:
    validate_mesh(mesh)
    lines = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$PhysicalNames", "2",
             '1 1 "frontiere"', '2 2 "domaine"', "$EndPhysicalNames",
             "$Nodes", str(len(mesh.points))]
    lines.extend(f"{i} {x:.17g} {y:.17g} 0" for i, (x, y) in enumerate(mesh.points, 1))
    lines.extend(["$EndNodes", "$Elements", str(len(mesh.boundary) + len(mesh.quads))])
    for index, (a, b) in enumerate(mesh.boundary, 1):
        lines.append(f"{index} 1 2 1 1 {a + 1} {b + 1}")
    for index, quad in enumerate(mesh.quads, len(mesh.boundary) + 1):
        nodes = " ".join(str(node + 1) for node in quad)
        lines.append(f"{index} 3 2 2 2 {nodes}")
    lines.extend(["$EndElements", ""])
    return "\n".join(lines)


def svg_text(mesh: Mesh, title: str = "Maillage quadrilatéral") -> str:
    validate_mesh(mesh)
    xs, ys = zip(*mesh.points)
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    scale = max(xmax - xmin, ymax - ymin)
    margin = scale * 0.035
    width, height = xmax - xmin + 2 * margin, ymax - ymin + 2 * margin
    stroke = scale * 0.0014
    escaped = html.escape(title, quote=True)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{xmin-margin:.17g} {-ymax-margin:.17g} {width:.17g} {height:.17g}" width="1000" height="800" role="img" aria-labelledby="title">',
             f'<title id="title">{escaped}</title>',
             f'<rect x="{xmin-margin:.17g}" y="{-ymax-margin:.17g}" width="{width:.17g}" height="{height:.17g}" fill="#f4f7fa"/>',
             f'<g stroke="#243c53" stroke-width="{stroke:.17g}" stroke-linejoin="round">']
    for quad in mesh.quads:
        coords = [mesh.points[node] for node in quad]
        coordinates = " ".join(f"{x:.17g},{-y:.17g}" for x, y in coords)
        lines.append(f'<polygon points="{coordinates}" fill="#8baec2"/>')
    lines.append("</g>")
    lines.append(f'<g stroke="#102a43" stroke-width="{stroke * 2.2:.17g}" fill="none">')
    for a, b in mesh.boundary:
        p, q = mesh.points[a], mesh.points[b]
        lines.append(f'<path d="M {p[0]:.17g} {-p[1]:.17g} L {q[0]:.17g} {-q[1]:.17g}"/>')
    lines.extend(["</g>", "</svg>", ""])
    return "\n".join(lines)


def export_mesh(mesh: Mesh, prefix: str | Path, title: str = "Maillage quadrilatéral") -> list[Path]:
    """Create three complete representations; each destination is replaced atomically."""
    root = str(prefix)
    documents = [(Path(root + ".json"), json.dumps(mesh_document(mesh), ensure_ascii=False,
                                                   allow_nan=False, indent=2, sort_keys=True) + "\n"),
                 (Path(root + ".msh"), gmsh_text(mesh)),
                 (Path(root + ".svg"), svg_text(mesh, title))]
    return [atomic_write(path, content) for path, content in documents]
