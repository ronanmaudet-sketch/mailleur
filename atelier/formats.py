"""Exports de maillages et groupes, avec limites solveur explicites.

Les formats de calcul ne contiennent ni matériau, ni chargement, ni condition
limite. ANSYS est un script APDL MESH200, pas le format Fluent nommé « ansys »
dans meshio. Le pont Cast3M utilise LIRE 'UNV', documenté par le CEA.
Les dépendances sont importées à la demande pour garder le diagnostic lisible.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata
import zipfile


FORMATS = {
    "msh": {"extension": ".msh", "label": "Gmsh natif", "solver": "Gmsh", "notes": "Copie native ; groupes physiques et ordre conservés."},
    "med": {"extension": ".med", "label": "MED / Salome", "solver": "Code_Aster, Salome-Meca, Cast3M", "notes": "Groupes d'éléments et de nœuds encodés comme familles MED."},
    "inp": {"extension": ".inp", "label": "Abaqus / CalculiX", "solver": "Abaqus, CalculiX", "notes": "Éléments de dimension maximale ; frontières dans des NSET. Formulation plane ou coque indiquée dans le manifeste."},
    "vtu": {"extension": ".vtu", "label": "VTK XML", "solver": "ParaView", "notes": "Groupes conservés comme masques group:nom ; format de visualisation."},
    "unv": {"extension": ".unv", "label": "I-DEAS Universal", "solver": "Salome, Cast3M et lecteurs UNV", "notes": "Écriture Gmsh ; groupes permanents. Vérifier les éléments autorisés par le lecteur cible."},
    "ansys": {"extension": ".dat", "label": "ANSYS APDL — maillage", "solver": "ANSYS Mechanical APDL", "notes": "Script MESH200 sans degrés de liberté : attribuer des éléments de calcul dans ANSYS avant résolution."},
    "ansys_structural": {"extension": ".apdl", "label": "ANSYS APDL — éléments structuraux", "solver": "ANSYS Mechanical APDL", "notes": "SOLID285/187/185, PLANE182 ou SHELL181 selon les cellules et le mode ; matériaux et sections à compléter. Sous-ensemble explicite, sans exécution ANSYS."},
    "nastran": {"extension": ".bdf", "label": "Nastran Bulk Data", "solver": "Nastran et lecteurs BDF compatibles", "notes": "Éléments 2D/3D linéaires et quadratiques pris en charge, SET1 pour les groupes ; propriétés et matériaux à définir. Coordonnées GRID* en champs de 16 caractères."},
    "lsdyna": {"extension": ".key", "label": "LS-DYNA Keyword — maillage", "solver": "LS-DYNA et lecteurs Keyword compatibles", "notes": "Triangles/quadrilatères et tétraèdres/prismes/hexaèdres linéaires ; groupes nommés. Les éléments quadratiques sont refusés sans conversion silencieuse."},
    "cast3m": {"extension": ".dgibi", "label": "Cast3M — import UNV", "solver": "Cast3M", "notes": "Script Gibiane d'import accompagné de son fichier UNV ; aucune hypothèse de matériau."},
}

_DIMS = {"vertex": 0, "line": 1, "line3": 1, "triangle": 2, "triangle6": 2,
         "quad": 2, "quad8": 2, "quad9": 2, "tetra": 3, "tetra10": 3,
         "hexahedron": 3, "hexahedron20": 3, "hexahedron27": 3,
         "wedge": 3, "wedge15": 3, "wedge18": 3, "pyramid": 3, "pyramid13": 3, "pyramid14": 3}


def _libraries():
    try:
        import meshio
        import numpy as np
    except ImportError as error:
        raise ValueError("Les exports nécessitent meshio et numpy ; lancer l'installation du projet.") from error
    return meshio, np


def _safe_names(names):
    result, used = {}, set()
    for original in sorted(names):
        clean = unicodedata.normalize("NFKD", original).encode("ascii", "ignore").decode()
        clean = re.sub(r"[^A-Za-z0-9_]", "_", clean).strip("_") or "GROUP"
        if not clean[0].isalpha() or clean.upper() in {"ALL", "STAT", "DEFA"}:
            clean = "G_" + clean
        base = clean[:28]
        name, counter = base, 1
        while name.upper() in used:
            suffix = f"_{counter}"
            name = base[:32-len(suffix)] + suffix
            counter += 1
        used.add(name.upper())
        result[original] = name
    return result


def _check_source_quality(source: Path):
    """Honor an adjacent atelier report without requiring one for external files.

    In particular, diagnostic candidates with ``strict=false`` may have a .msh
    on disk while still being refused by the quality policy. Converting that
    candidate must not bypass the acceptance decision.
    """
    report_path = source.parent / "report.json"
    if not report_path.exists() and not report_path.is_symlink():
        return
    if report_path.is_symlink() or not report_path.is_file():
        raise ValueError("Le rapport de qualité associé doit être un fichier JSON régulier.")
    from .projects import strict_json
    try:
        report = strict_json(report_path.read_bytes())
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        raise ValueError("Le rapport de qualité associé est illisible ou malformé ; export refusé.") from error
    if not isinstance(report, dict):
        raise ValueError("Le rapport de qualité associé doit être un objet JSON ; export refusé.")
    if "accepted" in report:
        if type(report["accepted"]) is not bool:
            raise ValueError("La décision du rapport associé est malformée ; export refusé.")
        if not report["accepted"]:
            raise ValueError("La recherche n'a pas trouvé de maillage admissible ; export interdit.")
    quality = report.get("quality")
    if quality is not None and not isinstance(quality, dict):
        raise ValueError("La qualité du rapport associé est malformée ; export refusé.")
    if isinstance(quality, dict) and "accepted" in quality:
        if type(quality["accepted"]) is not bool:
            raise ValueError("La décision de qualité associée est malformée ; export refusé.")
        if not quality["accepted"]:
            raise ValueError("Le maillage a été refusé par les critères de qualité ; export interdit.")
    if report.get("display_only") is True:
        raise ValueError("La tessellation d'examen est réservée à l'affichage ; générer un maillage de calcul avant l'export.")


def _read(source: Path):
    meshio, np = _libraries()
    if source.suffix.lower() != ".msh" or not source.is_file() or source.is_symlink():
        raise ValueError("Un fichier Gmsh .msh régulier est requis pour l'export.")
    # meshio prints a blank line while reading Gmsh; keep CLI JSON clean.
    with redirect_stdout(io.StringIO()):
        original = meshio.read(source, file_format="gmsh")
    points = np.asarray(original.points, dtype=float)
    if points.ndim != 2 or points.shape[1] not in (2, 3) or not len(points) or not np.isfinite(points).all():
        raise ValueError("Coordonnées de maillage absentes ou non finies.")
    if points.shape[1] == 2:
        points = np.column_stack((points, np.zeros(len(points))))
    if not original.cells:
        raise ValueError("Le maillage ne contient aucun élément.")
    kinds, parts, block_map = [], {}, []
    for block in original.cells:
        if block.type not in _DIMS:
            raise ValueError(f"Type d'élément non pris en charge : {block.type}.")
        data = np.asarray(block.data, dtype=np.int64)
        if data.ndim != 2 or not len(data) or (data < 0).any() or (data >= len(points)).any():
            raise ValueError("Connectivité de maillage invalide.")
        if block.type not in parts:
            kinds.append(block.type)
            parts[block.type] = []
        offset = sum(len(part) for part in parts[block.type])
        block_map.append((kinds.index(block.type), offset))
        parts[block.type].append(data)
    cells = [(kind, np.concatenate(parts[kind])) for kind in kinds]
    raw_sets = {}
    for name, per_block in original.cell_sets.items():
        if name.startswith("gmsh:"):
            continue
        raw_sets[name] = [np.empty(0, dtype=int) for _ in cells]
        for index, values in enumerate(per_block):
            if values is None:
                continue
            values = np.asarray(values, dtype=int)
            if values.ndim != 1 or (values < 0).any() or (values >= len(original.cells[index].data)).any():
                raise ValueError(f"Groupe d'éléments invalide : {name}.")
            dest, offset = block_map[index]
            raw_sets[name][dest] = np.union1d(raw_sets[name][dest], values + offset)
    physical = original.cell_data.get("gmsh:physical")
    for name, tag_dimension in original.field_data.items():
        if not isinstance(name, str) or len(tag_dimension) != 2 or physical is None:
            continue
        tag, dimension = map(int, tag_dimension)
        if name not in raw_sets:
            raw_sets[name] = [np.empty(0, dtype=int) for _ in cells]
        for index, block in enumerate(original.cells):
            if _DIMS[block.type] == dimension:
                dest, offset = block_map[index]
                selected = np.flatnonzero(np.asarray(physical[index]) == tag) + offset
                raw_sets[name][dest] = np.union1d(raw_sets[name][dest], selected)
    raw_points = {name: np.unique(np.asarray(values, dtype=int)) for name, values in original.point_sets.items()}
    names = _safe_names(set(raw_sets) | set(raw_points))
    cell_sets = {names[name]: values for name, values in raw_sets.items() if any(len(x) for x in values)}
    point_sets = {names[name]: values for name, values in raw_points.items() if len(values)}
    mesh = meshio.Mesh(points, cells, cell_sets=cell_sets, point_sets=point_sets)
    return mesh, names


def _summary(mesh):
    return {"nodes": len(mesh.points), "elements": sum(len(c.data) for c in mesh.cells),
            "cell_counts": {c.type: len(c.data) for c in mesh.cells}}


def _groups(mesh):
    return {"elements": {name: sum(len(v) for v in parts) for name, parts in mesh.cell_sets.items()},
            "node_sets": {name: len(values) for name, values in mesh.point_sets.items()}}


def _analysis_mesh(mesh):
    """Keep only analysis cells; represent lower-dimensional groups as node sets."""
    meshio, np = _libraries()
    dimension = max(_DIMS[c.type] for c in mesh.cells)
    selected = [i for i, cell in enumerate(mesh.cells) if _DIMS[cell.type] == dimension]
    cell_sets = {name: [parts[i] for i in selected] for name, parts in mesh.cell_sets.items()}
    cell_sets = {name: parts for name, parts in cell_sets.items() if any(len(x) for x in parts)}
    point_sets = {name: values.copy() for name, values in mesh.point_sets.items()}
    for name, parts in mesh.cell_sets.items():
        nodes = [mesh.cells[i].data[indices].ravel() for i, indices in enumerate(parts)
                 if len(indices) and _DIMS[mesh.cells[i].type] < dimension]
        if nodes:
            point_sets[name] = np.unique(np.concatenate([*nodes, point_sets.get(name, np.empty(0, dtype=int))]))
    return meshio.Mesh(mesh.points.copy(), [mesh.cells[i] for i in selected],
                       cell_sets=cell_sets, point_sets=point_sets), dimension


def _write_med(path, mesh):
    meshio, np = _libraries()
    try:
        import h5py
    except ImportError as error:
        raise ValueError("L'export MED nécessite h5py.") from error
    target = meshio.Mesh(mesh.points.copy(), [(c.type, c.data.copy()) for c in mesh.cells])
    families, family_lookup, tags = {}, {}, []
    for index, cell in enumerate(mesh.cells):
        memberships = [[] for _ in cell.data]
        for name, parts in mesh.cell_sets.items():
            for item in parts[index]:
                memberships[int(item)].append(name)
        values = np.zeros(len(cell.data), dtype=int)
        for item, names in enumerate(memberships):
            combination = tuple(sorted(names))
            if not combination:
                continue
            if combination not in family_lookup:
                number = -(len(families) + 1)
                family_lookup[combination] = number
                families[number] = list(combination)
            values[item] = family_lookup[combination]
        tags.append(values)
    target.cell_data["cell_tags"] = tags
    target.cell_tags = families
    point_memberships = [[] for _ in mesh.points]
    for name, indices in mesh.point_sets.items():
        for index in indices:
            point_memberships[int(index)].append(name)
    point_families, point_lookup = {}, {}
    values = np.zeros(len(mesh.points), dtype=int)
    for index, names in enumerate(point_memberships):
        combination = tuple(sorted(names))
        if combination:
            if combination not in point_lookup:
                number = len(point_families) + 1
                point_lookup[combination] = number
                point_families[number] = list(combination)
            values[index] = point_lookup[combination]
    if point_families:
        target.point_data["point_tags"] = values
        target.point_tags = point_families
    meshio.write(path, target, file_format="med")
    # meshio 5.3.5 uses spatial dimension for DIM; MED distinguishes it from ESP.
    with h5py.File(path, "r+") as stream:
        stream["ENS_MAA"]["mesh"].attrs.modify("DIM", max(_DIMS[c.type] for c in mesh.cells))
    restored = meshio.read(path, file_format="med")
    _check_geometry(mesh, restored)
    actual = {}
    for index, cell in enumerate(restored.cells):
        families_for_cells = restored.cell_data.get("cell_tags", [])[index]
        for family_id, names in restored.cell_tags.items():
            count = int(np.count_nonzero(families_for_cells == family_id))
            for name in names:
                actual[name] = actual.get(name, 0) + count
    expected = _groups(mesh)["elements"]
    if actual != expected:
        raise ValueError("La relecture MED ne conserve pas tous les groupes d'éléments.")
    return {"readback": True, "groups_readback": True, "reader": "meshio MED"}


def _write_vtu(path, mesh):
    meshio, np = _libraries()
    cell_data = {}
    for name, parts in mesh.cell_sets.items():
        arrays = []
        for cell, indices in zip(mesh.cells, parts):
            values = np.zeros(len(cell.data), dtype=np.uint8)
            values[indices] = 1
            arrays.append(values)
        cell_data["group:" + name] = arrays
    point_data = {}
    for name, indices in mesh.point_sets.items():
        values = np.zeros(len(mesh.points), dtype=np.uint8)
        values[indices] = 1
        point_data["group:" + name] = values
    target = meshio.Mesh(mesh.points.copy(), [(c.type, c.data.copy()) for c in mesh.cells],
                         cell_data=cell_data, point_data=point_data)
    meshio.write(path, target, file_format="vtu")
    restored = meshio.read(path, file_format="vtu")
    _check_geometry(mesh, restored)
    for name, parts in mesh.cell_sets.items():
        actual = sum(int(np.count_nonzero(values)) for values in restored.cell_data["group:" + name])
        if actual != sum(len(p) for p in parts):
            raise ValueError("La relecture VTU ne conserve pas tous les masques de groupes.")
    return {"readback": True, "groups_readback": True, "reader": "meshio VTU"}


def _check_geometry(expected, actual, *, rtol=1e-13, atol=1e-13):
    _, np = _libraries()
    if _summary(expected) != _summary(actual) or not np.allclose(expected.points, actual.points, rtol=rtol, atol=atol):
        raise ValueError("La relecture de l'export a modifié les coordonnées ou le nombre d'éléments.")
    actual_cells = {c.type: c.data for c in actual.cells}
    for cell in expected.cells:
        if not np.array_equal(cell.data, actual_cells[cell.type]):
            raise ValueError("La relecture de l'export a modifié la connectivité ou l'ordre des nœuds.")


_INP_SOLIDS = {"tetra": "C3D4", "tetra10": "C3D10", "hexahedron": "C3D8", "hexahedron20": "C3D20",
               "wedge": "C3D6", "wedge15": "C3D15", "pyramid": "C3D5"}
_INP_PLANAR = {"triangle": "CPS3", "triangle6": "CPS6", "quad": "CPS4", "quad8": "CPS8"}
_INP_SHELLS = {"triangle": "S3", "triangle6": "S6", "quad": "S4", "quad8": "S8R"}


def _inp_real(value):
    """Fit the 20-character real fields shared by Abaqus and CalculiX.

    A Python .17g value may exceed that limit, notably with a negative sign or
    an exponent. Keep as many significant digits as the solver can read.
    """
    import math
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Coordonnée INP non finie.")
    for precision in range(17, 12, -1):
        result = format(value, f".{precision}g")
        if "e" in result:
            mantissa, exponent = result.split("e")
            result = f"{mantissa}e{int(exponent)}"
        if len(result) <= 20:
            return result
    raise ValueError("Coordonnée INP impossible à représenter dans 20 caractères.")


def _write_inp(path, mesh, planar):
    analysis, dimension = _analysis_mesh(mesh)
    if dimension < 2:
        raise ValueError("L'export INP prend en charge les maillages surfaciques et volumiques.")
    mapping = _INP_SOLIDS if dimension == 3 else (_INP_PLANAR if planar else _INP_SHELLS)
    unsupported = [c.type for c in analysis.cells if c.type not in mapping]
    if unsupported:
        raise ValueError("Types INP non pris en charge sans perte d'ordre : " + ", ".join(unsupported))
    if planar and dimension == 2 and not all(abs(p[2]) <= 1e-12 * max(1.0, abs(p[0]), abs(p[1])) for p in analysis.points):
        raise ValueError("L'export plan CPS nécessite un maillage dans le plan XY.")
    lines = ["*HEADING", "Atelier Mailleur - mesh only; add materials, sections and boundary conditions", "*NODE"]
    lines.extend(f"{i},{_inp_real(p[0])},{_inp_real(p[1])},{_inp_real(p[2])}" for i, p in enumerate(analysis.points, 1))
    offset, block_offsets = 0, []
    for cell in analysis.cells:
        lines.append(f"*ELEMENT, TYPE={mapping[cell.type]}")
        block_offsets.append(offset)
        for row in cell.data:
            offset += 1
            ids = [str(int(v) + 1) for v in row]
            first = str(offset) + "," + ",".join(ids[:15])
            lines.append(first + ("," if len(ids) > 15 else ""))
            for start in range(15, len(ids), 16):
                tail = ",".join(ids[start:start+16])
                lines.append(tail + ("," if start + 16 < len(ids) else ""))
    for name, parts in analysis.cell_sets.items():
        indices = [int(v) + block_offsets[i] + 1 for i, values in enumerate(parts) for v in values]
        lines.append(f"*ELSET, ELSET={name}")
        lines.extend(",".join(map(str, indices[i:i+12])) for i in range(0, len(indices), 12))
    for name, values in analysis.point_sets.items():
        indices = [int(v) + 1 for v in values]
        lines.append(f"*NSET, NSET={name}")
        lines.extend(",".join(map(str, indices[i:i+12])) for i in range(0, len(indices), 12))
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    warnings = [
        "Les groupes de frontière deviennent des NSET, pas des faces chargées ni des éléments supplémentaires.",
        "Le choix CPS correspond aux contraintes planes ; modifier la formulation pour déformations planes, thermique ou autre physique."
        if planar and dimension == 2 else "Matériaux, sections/épaisseurs et conditions limites sont à définir dans le solveur."]
    if any(c.type == "pyramid" for c in analysis.cells):
        warnings.append("Les pyramides C3D5 ciblent Abaqus ; leur prise en charge dans CalculiX n'est pas assurée.")
    return analysis, {"readback": False, "groups_readback": False, "writer_checks": True,
                       "formulation": "solid" if dimension == 3 else ("plane_stress" if planar else "shell"),
                       "numeric_field_max_characters": 20,
                       "coordinate_significant_digits": "13–17 selon signe et exposant",
                       "element_types": {c.type: mapping[c.type] for c in analysis.cells}}, warnings


_APDL = {"triangle": 4, "triangle6": 5, "quad": 6, "quad8": 7,
         "tetra": 8, "tetra10": 9, "hexahedron": 10, "hexahedron20": 11}


def _apdl_group(lines, name, values, entity):
    selector = "NSEL" if entity == "NODE" else "ESEL"
    for index, value in enumerate(values):
        lines.append(f"{selector},{'S' if index == 0 else 'A'},{entity},,{int(value)}")
    lines.append(f"CM,{name},{entity}")
    lines.append("ALLSEL,ALL")


def _write_apdl(path, mesh):
    analysis, _ = _analysis_mesh(mesh)
    unsupported = [c.type for c in analysis.cells if c.type not in _APDL]
    if unsupported:
        raise ValueError("Types APDL MESH200 non pris en charge : " + ", ".join(unsupported))
    lines = ["! Atelier Mailleur - topology import, NOT a solved FE model", "/PREP7",
             "! MESH200 has no degrees of freedom. Assign suitable FE types before solution."]
    for index, cell in enumerate(analysis.cells, 1):
        lines.extend([f"ET,{index},200", f"KEYOPT,{index},1,{_APDL[cell.type]}"])
    for index, point in enumerate(analysis.points, 1):
        lines.append(f"N,{index},{point[0]:.17g},{point[1]:.17g},{point[2]:.17g}")
    offset, block_offsets = 0, []
    for index, cell in enumerate(analysis.cells, 1):
        block_offsets.append(offset)
        lines.append(f"TYPE,{index}")
        for row in cell.data:
            offset += 1
            ids = [int(v) + 1 for v in row]
            lines.append(f"EN,{offset}," + ",".join(map(str, ids[:8])))
            for start in range(8, len(ids), 8):
                lines.append("EMORE," + ",".join(map(str, ids[start:start+8])))
    for name, parts in analysis.cell_sets.items():
        values = [int(v) + block_offsets[i] + 1 for i, indices in enumerate(parts) for v in indices]
        _apdl_group(lines, name, values, "ELEM")
    for name, indices in analysis.point_sets.items():
        _apdl_group(lines, "N_" + name[:30], [int(v) + 1 for v in indices], "NODE")
    lines.extend(["ALLSEL,ALL", "FINISH", ""])
    path.write_text("\n".join(lines), encoding="ascii")
    return analysis, {"readback": False, "groups_readback": False, "writer_checks": True,
                       "element_type": "MESH200", "element_shape_options": {c.type: _APDL[c.type] for c in analysis.cells}}, [
        "MESH200 n'a pas de degrés de liberté : le fichier importe la topologie, puis les types d'éléments de calcul doivent être affectés dans ANSYS.",
        "Les groupes de frontière deviennent des composants nodaux préfixés N_, sans chargement de face automatique.",
        "Script APDL vérifié structurellement ; aucune exécution ANSYS n'a été réalisée ici."]


def _solver_groups(mesh):
    """Groupes renumérotés dans l'ordre effectivement écrit des éléments."""
    offsets, offset = [], 0
    for cell in mesh.cells:
        offsets.append(offset)
        offset += len(cell.data)
    groups = []
    for name, parts in sorted(mesh.cell_sets.items()):
        ids = sorted({int(value) + offsets[index] + 1 for index, values in enumerate(parts) for value in values})
        if ids:
            groups.append({"name": name, "entity": "ELEM", "ids": ids})
    for name, values in sorted(mesh.point_sets.items()):
        ids = sorted({int(value) + 1 for value in values})
        if ids:
            groups.append({"name": name, "entity": "NODE", "ids": ids})
    return groups


def _apdl_structural_definition(kind, dimension, planar):
    # ANSYS Element Reference : seules les formes dont l'ordre est explicite
    # sont admises. Pas de suppression de nœuds intermédiaires.
    if dimension == 3:
        definitions = {"tetra": (285, [(1, 1)]), "tetra10": (187, []), "hexahedron": (185, [])}
    elif dimension == 2 and planar:
        definitions = {"triangle": (182, [(3, 0)]), "quad": (182, [(3, 0)])}
    elif dimension == 2:
        # SHELL181 triangulaire n'est recommandé que comme remplissage ; ce
        # parcours ne transforme pas un domaine triangulé entier en remplissage.
        definitions = {"quad": (181, [])}
    else:
        definitions = {}
    if kind not in definitions:
        raise ValueError(f"Type ANSYS structural non validé pour ce mode : {kind}. "
                         "Utiliser ansys/MESH200 ou un autre format sans réduction d'ordre.")
    return definitions[kind]


def _write_apdl_structural(path, mesh, planar):
    analysis, dimension = _analysis_mesh(mesh)
    definitions = {cell.type: _apdl_structural_definition(cell.type, dimension, planar) for cell in analysis.cells}
    if planar and dimension == 2 and any(abs(point[2]) > 1e-12 * max(1., abs(point[0]), abs(point[1])) for point in analysis.points):
        raise ValueError("PLANE182 nécessite un maillage dans le plan XY.")
    lines = ["! Atelier Mailleur - structural mesh, NOT a complete analysis", "/PREP7",
             "! Add materials, shell sections/thickness, loads and boundary conditions."]
    for index, cell in enumerate(analysis.cells, 1):
        element, options = definitions[cell.type]
        lines.append(f"ET,{index},{element}")
        lines.extend(f"KEYOPT,{index},{key},{value}" for key, value in options)
    for index, point in enumerate(analysis.points, 1):
        lines.append(f"N,{index},{point[0]:.17g},{point[1]:.17g},{point[2]:.17g}")
    offset = 0
    for index, cell in enumerate(analysis.cells, 1):
        lines.append(f"TYPE,{index}")
        for row in cell.data:
            offset += 1
            ids = [int(value) + 1 for value in row]
            if cell.type == "triangle":
                ids.append(ids[2])  # PLANE182 : K=L, forme dégénérée documentée.
            lines.append(f"EN,{offset}," + ",".join(map(str, ids[:8])))
            for start in range(8, len(ids), 8):
                lines.append("EMORE," + ",".join(map(str, ids[start:start + 8])))
    components, used = [], set()
    for group in _solver_groups(analysis):
        base = group["name"] if group["entity"] == "ELEM" else "N_" + group["name"]
        component, number = base[:32], 1
        while component.upper() in used:
            number += 1
            suffix = f"_{number}"
            component = base[:32 - len(suffix)] + suffix
        used.add(component.upper())
        _apdl_group(lines, component, group["ids"], group["entity"])
        components.append({"name": group["name"], "entity": group["entity"], "component": component})
    lines.extend(["ALLSEL,ALL", "FINISH", ""])
    path.write_text("\n".join(lines), encoding="ascii")
    warnings = ["Script structural contrôlé sans exécution dans ANSYS ; compléter matériaux, sections et conditions limites.",
                "Les frontières sont des composants nodaux, sans pression ni contact automatique."]
    if dimension == 2:
        warnings.append("PLANE182 est réglé en contraintes planes." if planar else "SHELL181 nécessite une section de coque et une épaisseur dans le solveur.")
    if "tetra" in definitions:
        warnings.append("SOLID285 est réglé en déplacement pur (KEYOPT(1)=1), pour élasticité linéaire ou très petites déformations plastiques ; adapter la formulation pour les autres analyses.")
    return analysis, {"readback": False, "groups_readback": False, "writer_checks": True, "solver_executed": False,
                      "formulation": "solid" if dimension == 3 else "plane_stress" if planar else "shell",
                      "element_types": {kind: f"{'SOLID' if dimension == 3 else 'PLANE' if planar else 'SHELL'}{value[0]}" for kind, value in definitions.items()},
                      "component_name_map": components}, warnings


_NASTRAN_TYPES = {"triangle": "CTRIA3", "triangle6": "CTRIA6", "quad": "CQUAD4", "quad8": "CQUAD8",
                  "tetra": "CTETRA", "tetra10": "CTETRA", "hexahedron": "CHEXA", "hexahedron20": "CHEXA",
                  "wedge": "CPENTA"}
# VTK/meshio -> Nastran : arêtes verticales avant les arêtes supérieures.
_NASTRAN_ORDER = {"hexahedron20": [0,1,2,3,4,5,6,7,8,9,10,11,16,17,18,19,12,13,14,15]}


def _nastran_real(value):
    """Champ réel GRID* de 16 caractères, exposant implicite Nastran."""
    _, np = _libraries()
    for precision in range(16, -1, -1):
        mantissa, exponent = np.format_float_scientific(float(value), precision=precision, unique=False,
                                                       trim="-", exp_digits=1).split("e")
        result = mantissa + ("" if "." in mantissa else ".") + exponent
        if len(result) <= 16:
            return result
    raise ValueError("Coordonnée non représentable dans un champ Nastran de 16 caractères.")


def _small_bdf_card(name, values):
    if any(not 0 < int(value) <= 99_999_999 for value in values):
        raise ValueError("Identifiant hors plage Nastran small-field (1 à 99999999).")
    return [name.ljust(8) + "".join(f"{int(value):>8}" for value in values[:8]),
            *("+".ljust(8) + "".join(f"{int(value):>8}" for value in values[index:index + 8])
              for index in range(8, len(values), 8))]


def _read_bdf_sets(path):
    groups, current, metadata = {}, None, {}
    for line in path.read_text(encoding="ascii").splitlines():
        if line.startswith("$ ATELIER_SET "):
            _, _, identifier, entity, name = line.split()
            metadata[int(identifier)] = (entity, name)
        elif line[:8].strip() == "SET1":
            values = [int(line[index:index + 8]) for index in range(8, len(line), 8) if line[index:index + 8].strip()]
            current = values[0]
            groups[current] = values[1:]
        elif current is not None and line[:8].strip() == "+":
            groups[current].extend(int(line[index:index + 8]) for index in range(8, len(line), 8) if line[index:index + 8].strip())
        elif not line.startswith("$"):
            current = None
    if set(groups) != set(metadata):
        raise ValueError("Les ensembles SET1 et leurs métadonnées ne correspondent pas.")
    return {(entity, name): groups[identifier] for identifier, (entity, name) in metadata.items()}


def _write_nastran(path, mesh):
    meshio, np = _libraries()
    analysis, dimension = _analysis_mesh(mesh)
    unsupported = [cell.type for cell in analysis.cells if cell.type not in _NASTRAN_TYPES]
    if dimension < 2 or unsupported:
        raise ValueError("Types Nastran BDF non pris en charge sans perte d'ordre : " + ", ".join(unsupported or ["dimension inférieure à 2"]))
    if len(analysis.points) > 99_999_999:
        raise ValueError("Trop de nœuds pour les identifiants Nastran choisis.")
    groups = _solver_groups(analysis)
    with path.open("w", encoding="ascii") as stream:
        stream.write("$ Atelier Mailleur - mesh only; define properties and materials\nBEGIN BULK\n")
        for identifier, point in enumerate(analysis.points, 1):
            x, y, z = (_nastran_real(value) for value in point)
            stream.write(f"{'GRID*':8}{identifier:16}{'':16}{x:>16}{y:>16}\n{'*':8}{z:>16}\n")
        element_id = 0
        for property_id, cell in enumerate(analysis.cells, 1):
            stream.write(f"$ ATELIER_PROPERTY {property_id} {cell.type}\n")
            permutation = _NASTRAN_ORDER.get(cell.type, list(range(cell.data.shape[1])))
            for row in cell.data:
                element_id += 1
                stream.write("\n".join(_small_bdf_card(_NASTRAN_TYPES[cell.type],
                             [element_id, property_id, *(int(row[index]) + 1 for index in permutation)])) + "\n")
        for identifier, group in enumerate(groups, 1):
            stream.write(f"$ ATELIER_SET {identifier} {group['entity']} {group['name']}\n")
            stream.write("\n".join(_small_bdf_card("SET1", [identifier, *group["ids"]])) + "\n")
        stream.write("ENDDATA\n")
    restored = meshio.read(path, file_format="nastran")
    _check_geometry(analysis, restored, rtol=5e-10, atol=1e-300)
    expected = {(group["entity"], group["name"]): group["ids"] for group in groups}
    if _read_bdf_sets(path) != expected:
        raise ValueError("La relecture BDF ne conserve pas tous les ensembles SET1.")
    difference = np.abs(analysis.points - restored.points)
    return analysis, {"readback": True, "groups_readback": True, "reader": "meshio Nastran + lecteur SET1 Atelier",
                      "solver_executed": False, "coordinate_rtol": 5e-10, "coordinate_atol": 1e-300,
                      "maximum_coordinate_error": float(difference.max()),
                      "set_id_map": [{"id": index, "entity": group["entity"], "name": group["name"]} for index, group in enumerate(groups, 1)],
                      "property_id_map": {cell.type: index for index, cell in enumerate(analysis.cells, 1)}}, [
        "BDF de maillage : compléter les propriétés PID, matériaux, cas de charge et conditions limites.",
        "SET1 ne distingue pas seul nœuds et éléments : utiliser les commentaires ATELIER_SET et set_id_map du manifeste.",
        "Coordonnées en champs de 16 caractères, erreur de sérialisation contrôlée à 5e-10 relative ; aucune unité n'est imposée par BDF.",
        "Relecture de format effectuée ; aucun solveur Nastran ni chaque version 2020+ n'a été exécuté."]


_KEY_TYPES = {"triangle", "quad", "tetra", "wedge", "hexahedron"}


def _read_key_subset(path):
    """Relire notre sous-ensemble Keyword, pas un lecteur LS-DYNA général."""
    meshio, np = _libraries()
    sections, rows, keyword = [], [], None
    for line in path.read_text(encoding="ascii").splitlines():
        if not line.strip() or line.startswith("$"):
            continue
        if line.startswith("*"):
            if keyword is not None:
                sections.append((keyword, rows))
            keyword, rows = line.strip().upper(), []
        else:
            rows.append(line.strip())
    if keyword is not None:
        sections.append((keyword, rows))
    points, cells, sets, element_id = [], {}, {}, 0
    for keyword, rows in sections:
        if keyword == "*NODE":
            for line in rows:
                values = line.split(",")
                if int(values[0]) != len(points) + 1 or len(values) != 4:
                    raise ValueError("Numérotation ou coordonnée Keyword invalide.")
                points.append(list(map(float, values[1:])))
        elif keyword in ("*ELEMENT_SHELL", "*ELEMENT_SOLID"):
            for line in rows:
                values = list(map(int, line.split(",")))
                element_id += 1
                if values[0] != element_id or values[1] < 1:
                    raise ValueError("Identifiant d'élément ou de PART Keyword invalide.")
                ids = values[2:]
                if keyword == "*ELEMENT_SHELL" and len(ids) == 4:
                    kind = "triangle" if ids[2] == ids[3] else "quad"
                    ids = ids[:3] if kind == "triangle" else ids
                elif keyword == "*ELEMENT_SOLID" and len(ids) == 8:
                    if ids[3:] == [ids[3]] * 5:
                        kind, ids = "tetra", ids[:4]
                    elif ids[4] == ids[5] and ids[6] == ids[7]:
                        kind, ids = "wedge", [*ids[:5], ids[6]]
                    else:
                        kind = "hexahedron"
                else:
                    raise ValueError("Carte élément Keyword hors sous-ensemble contrôlé.")
                cells.setdefault(kind, []).append([value - 1 for value in ids])
        elif keyword in ("*SET_NODE_LIST_TITLE", "*SET_SHELL_LIST_TITLE", "*SET_SOLID_TITLE"):
            if len(rows) < 3:
                raise ValueError("Ensemble Keyword incomplet.")
            entity = "NODE" if keyword == "*SET_NODE_LIST_TITLE" else "ELEM"
            sets[(entity, rows[0])] = [int(value) for line in rows[2:] for value in line.split(",") if value.strip()]
    return meshio.Mesh(np.asarray(points), [(kind, np.asarray(values, dtype=int)) for kind, values in cells.items()]), sets


def _write_lsdyna(path, mesh):
    analysis, dimension = _analysis_mesh(mesh)
    unsupported = [cell.type for cell in analysis.cells if cell.type not in _KEY_TYPES]
    if dimension < 2 or unsupported:
        raise ValueError("Types LS-DYNA Keyword non pris en charge sans perte d'ordre : " + ", ".join(unsupported or ["dimension inférieure à 2"]))
    groups = _solver_groups(analysis)
    with path.open("w", encoding="ascii") as stream:
        stream.write("*KEYWORD\n$ Atelier Mailleur - mesh only; define PART, SECTION and MAT\n*NODE\n")
        for identifier, point in enumerate(analysis.points, 1):
            stream.write(f"{identifier},{point[0]:.17g},{point[1]:.17g},{point[2]:.17g}\n")
        element_id = 0
        for part_id, cell in enumerate(analysis.cells, 1):
            stream.write(f"$ ATELIER_PART {part_id} {cell.type}\n{'*ELEMENT_SOLID' if dimension == 3 else '*ELEMENT_SHELL'}\n")
            for row in cell.data:
                element_id += 1
                ids = [int(value) + 1 for value in row]
                if cell.type == "triangle":
                    ids.append(ids[2])
                elif cell.type == "tetra":
                    ids.extend([ids[3]] * 4)
                elif cell.type == "wedge":
                    ids = [*ids[:5], ids[4], ids[5], ids[5]]
                stream.write(",".join(map(str, [element_id, part_id, *ids])) + "\n")
        for identifier, group in enumerate(groups, 1):
            keyword = "*SET_NODE_LIST_TITLE" if group["entity"] == "NODE" else "*SET_SOLID_TITLE" if dimension == 3 else "*SET_SHELL_LIST_TITLE"
            stream.write(f"{keyword}\n{group['name']}\n{identifier}\n")
            for index in range(0, len(group["ids"]), 8):
                stream.write(",".join(map(str, group["ids"][index:index + 8])) + "\n")
        stream.write("*END\n")
    restored, actual_groups = _read_key_subset(path)
    _check_geometry(analysis, restored)
    if actual_groups != {(group["entity"], group["name"]): group["ids"] for group in groups}:
        raise ValueError("La relecture Keyword ne conserve pas tous les ensembles.")
    return analysis, {"readback": True, "groups_readback": True, "reader": "Atelier Keyword subset parser",
                      "solver_executed": False, "part_id_map": {cell.type: index for index, cell in enumerate(analysis.cells, 1)}}, [
        "Fragment de maillage Keyword : définir PART, SECTION/ELFORM, MAT, épaisseurs et conditions limites dans le solveur.",
        "Les groupes recouvrants restent des SET nommés ; ils ne deviennent pas automatiquement des PART ni des contacts.",
        "Les triangles et solides dégénérés utilisent les répétitions nodales documentées ; vérifier la formulation ELFORM retenue.",
        "Relecture par un lecteur interne du sous-ensemble exporté ; LS-DYNA et les différentes versions 2020+ n'ont pas été exécutés."]


def _write_unv(source, path):
    if os.environ.get("ATELIER_NATIVE_EXPORT") == "1":
        import subprocess
        from .native import core_path, gmsh_library
        request = path.parent / (".unv_request_" + path.stem + ".json")
        request.write_text(json.dumps({"operation": "export_unv", "gmsh_library": str(gmsh_library()),
                                       "source_mesh": str(Path(source).resolve()), "output_mesh": str(path.resolve())}), encoding="utf-8")
        try:
            run = subprocess.run([str(core_path()), "--request", str(request)], capture_output=True, text=True, timeout=120)
            if run.returncode:
                message = run.stderr.strip() or f"Le processus d'export natif a échoué ({run.returncode})"
                try: message = json.loads(message).get("error", message)
                except ValueError: pass
                raise ValueError(message)
            return json.loads(run.stdout.splitlines()[-1])["result"]
        finally:
            request.unlink(missing_ok=True)
    try:
        import gmsh
    except (ImportError, OSError) as error:
        raise ValueError(f"L'export UNV nécessite Gmsh et ses bibliothèques système : {error}") from error
    if gmsh.isInitialized():
        raise ValueError("L'export UNV doit être exécuté dans un processus Gmsh indépendant.")
    gmsh.initialize(["atelier-export", "-nopopup"], readConfigFiles=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.open(str(source))
        _, before_tags, _ = gmsh.model.mesh.getElements()
        before = sum(len(v) for v in before_tags)
        gmsh.option.setNumber("Mesh.SaveGroupsOfNodes", 1)
        gmsh.option.setNumber("Mesh.SaveGroupsOfElements", 1)
        gmsh.write(str(path))
        gmsh.clear()
        gmsh.open(str(path))
        _, after_tags, _ = gmsh.model.mesh.getElements()
        after = sum(len(v) for v in after_tags)
        if before != after:
            raise ValueError(f"La relecture UNV a changé le nombre d'éléments ({before} → {after}).")
        names = [gmsh.model.getPhysicalName(dim, tag) for dim, tag in gmsh.model.getPhysicalGroups()]
        return {"readback": True, "groups_readback": False, "reader": "Gmsh UNV", "physical_names_read": names,
                "elements_read": after}
    finally:
        gmsh.finalize()


def convert_mesh(source: Path, target: Path, format_name: str, *, planar: bool = False) -> dict:
    """Convert a native .msh, retaining order or raising an actionable ValueError.

    ``planar`` selects INP and APDL structural formulations. The legacy
    ``ansys`` key remains a topology-only MESH200 script. Return values never
    claim an external solver was executed.
    Destination files are replaced only after successful writing and checks.
    """
    if format_name not in FORMATS:
        raise ValueError(f"Format inconnu : {format_name}.")
    source, target = Path(source), Path(target)
    if source.resolve() == target.resolve():
        raise ValueError("La destination doit être différente du maillage source.")
    if target.suffix.lower() != FORMATS[format_name]["extension"]:
        raise ValueError(f"Extension attendue pour {format_name} : {FORMATS[format_name]['extension']}.")
    _check_source_quality(source)
    mesh, names = _read(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".export_", suffix=target.suffix, dir=target.parent)
    os.close(descriptor)
    temp = Path(temporary)
    result_mesh, warnings = mesh, []
    files = [target.name]
    try:
        if format_name == "msh":
            shutil.copyfile(source, temp)
            verification = {"byte_identical": source.read_bytes() == temp.read_bytes(), "readback": True,
                            "groups_readback": True, "reader": "native Gmsh copy"}
        elif format_name == "med":
            verification = _write_med(temp, mesh)
        elif format_name == "vtu":
            verification = _write_vtu(temp, mesh)
        elif format_name == "inp":
            result_mesh, verification, warnings = _write_inp(temp, mesh, bool(planar))
        elif format_name == "ansys":
            result_mesh, verification, warnings = _write_apdl(temp, mesh)
        elif format_name == "ansys_structural":
            result_mesh, verification, warnings = _write_apdl_structural(temp, mesh, bool(planar))
        elif format_name == "nastran":
            result_mesh, verification, warnings = _write_nastran(temp, mesh)
        elif format_name == "lsdyna":
            result_mesh, verification, warnings = _write_lsdyna(temp, mesh)
        elif format_name == "unv":
            verification = _write_unv(source, temp)
            warnings.append("Groupes UNV écrits par Gmsh ; leur interprétation doit être vérifiée dans la version du lecteur cible.")
        else:
            unv = target.with_suffix(".unv")
            if any(c in unv.name for c in "'\r\n"):
                raise ValueError("Le nom du fichier Cast3M ne peut contenir ni apostrophe ni saut de ligne.")
            unv_result = convert_mesh(source, unv, "unv")
            temp.write_text("* Atelier Mailleur : importer depuis le dossier contenant le fichier UNV.\n"
                            f"TAB1 = LIRE 'UNV' '{unv.name}' ;\n"
                            "MAIL1 = TAB1 . 'MAILLAGE' ;\n"
                            "* Completer ensuite modele, materiau, chargement et conditions limites.\n", encoding="ascii")
            files.append(unv.name)
            verification = dict(unv_result["verification"], cast3m_executed=False)
            warnings.extend(unv_result["warnings"])
            warnings.append("Le script Gibiane doit être lancé dans Cast3M depuis le dossier de son fichier UNV ; Cast3M n'a pas été exécuté ici.")
        os.replace(temp, target)
    except (KeyError, RuntimeError, ImportError) as error:
        raise ValueError(f"Export {format_name} impossible sans perte déclarée : {error}") from error
    finally:
        temp.unlink(missing_ok=True)
    return {"format": format_name, "status": "ok", "file": target.name, "files": files,
            **_summary(result_mesh), "groups": _groups(result_mesh), "group_name_map": names,
            "warnings": warnings, "verification": verification}


def export_bundle(source: Path, output_dir: Path, formats: list[str] | None = None, *, planar: bool = False) -> dict:
    """Export independent formats and a ZIP; report each failure explicitly."""
    source = Path(source)
    _check_source_quality(source)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    requested = list(FORMATS) if formats is None else list(dict.fromkeys(formats))
    if not requested or any(name not in FORMATS for name in requested):
        raise ValueError("Sélection de formats vide ou inconnue.")
    results, files = [], set()
    for name in requested:
        target = output_dir / ("import_cast3m.dgibi" if name == "cast3m" else "mesh" + FORMATS[name]["extension"])
        try:
            result = convert_mesh(source, target, name, planar=planar)
            files.update(result["files"])
        except (ValueError, OSError) as error:
            result = {"format": name, "status": "error", "error": str(error)}
        results.append(result)
    mesh, names = _read(Path(source))
    groups = {"schema": "atelier.groups.v1", "group_name_map": names, "groups": _groups(mesh),
              "cell_types": [c.type for c in mesh.cells],
              "element_indices": {name: [list(map(int, values)) for values in parts] for name, parts in mesh.cell_sets.items()},
              "node_indices": {name: list(map(int, values)) for name, values in mesh.point_sets.items()},
              "index_base": 0, "note": "Les indices d'éléments sont locaux aux blocs fusionnés par type ; les exports solveur renumérotent les éléments."}
    (output_dir / "groups.json").write_text(json.dumps(groups, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files.add("groups.json")
    report = {"schema": "atelier.exports.v1", "complete": all(r["status"] == "ok" for r in results),
              "archive": "exports.zip", "results": results, "files": sorted(files | {"export_manifest.json"}),
              "warnings": ["Compatibilité de format vérifiée ; aucun calcul n'a été exécuté dans les solveurs destinataires."]}
    (output_dir / "export_manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with zipfile.ZipFile(output_dir / "exports.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in report["files"]:
            archive.write(output_dir / name, name)
    return report
