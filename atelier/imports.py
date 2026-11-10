"""Lecture de fichiers et passerelles locales ; préparation géométrique en Rust.

Ce module ne lance aucune commande provenant d'un projet ou d'un fichier reçu.
Les commandes de conversion sont définies exclusivement par la configuration
locale MAILLEUR_CAD_CONVERTERS choisie par le propriétaire de l'installation.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET

from .import_registry import (CAD_EXTENSIONS, CONVERTER_EXTENSIONS, MESH_READERS,
                              NATIVE_GMSH_EXTENSIONS, SOURCE_EXTENSIONS, source_extension)
from .projects import strict_json, safe_filename
from mailleur.io import write_json

MAX_SOURCE_BYTES = 200 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_POINTS = 2_000_000
MAX_CELLS = 2_000_000
MAX_INSTANCES = 2048
CONVERTER_OUTPUTS = CAD_EXTENSIONS | {".stl", ".msh"}
UNIT_METRES = {"m": 1.0, "cm": .01, "mm": .001, "in": .0254}
UNIT_3MF = {"micron": 1e-6, "millimeter": .001, "centimeter": .01,
            "inch": .0254, "foot": .3048, "meter": 1.0}


def load_converters():
    location = os.environ.get("MAILLEUR_CAD_CONVERTERS")
    if not location:
        return []
    config_path = Path(location).expanduser().resolve()
    if not config_path.is_file() or config_path.stat().st_size > 1024 * 1024:
        raise ValueError("MAILLEUR_CAD_CONVERTERS doit désigner un fichier JSON local de moins de 1 Mio")
    data = strict_json(config_path.read_bytes())
    if not isinstance(data, dict) or data.get("schema") != "atelier.cad_converters.v1":
        raise ValueError("Schéma de configuration des convertisseurs inconnu")
    rows = data.get("converters")
    if not isinstance(rows, list) or len(rows) > 64:
        raise ValueError("converters doit contenir au plus 64 configurations")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Chaque convertisseur doit être un objet")
        name, extensions, command = row.get("name"), row.get("extensions"), row.get("command")
        if not isinstance(name, str) or not 1 <= len(name) <= 160:
            raise ValueError("Nom de convertisseur invalide")
        if (not isinstance(extensions, list) or not extensions
                or any(not isinstance(ext, str) or ext not in CONVERTER_EXTENSIONS for ext in extensions)):
            raise ValueError("Extensions du convertisseur inconnues ou déjà traitées nativement")
        if seen.intersection(extensions) or len(set(extensions)) != len(extensions):
            raise ValueError("Plusieurs convertisseurs revendiquent la même extension")
        if (not isinstance(command, list) or not 1 <= len(command) <= 64
                or any(not isinstance(arg, str) or not arg or len(arg) > 4096 or "\0" in arg for arg in command)):
            raise ValueError("command doit être une liste non vide d'arguments sans shell")
        if "{input}" not in command or "{output}" not in command:
            raise ValueError("command doit inclure les arguments séparés {input} et {output}")
        for arg in command:
            unknown = set(re.findall(r"\{[^{}]*\}", arg)) - {"{input}", "{output}"}
            if unknown or (("{input}" in arg or "{output}" in arg) and arg not in ("{input}", "{output}")):
                raise ValueError("Les variables {input}/{output} doivent être des arguments séparés")
        output = row.get("output_extension", ".step")
        if output not in CONVERTER_OUTPUTS:
            raise ValueError("La passerelle doit produire STEP, IGES, BREP, STL ou MSH")
        timeout = row.get("timeout", 120)
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise ValueError("Le délai du convertisseur doit être un entier de 1 à 3600 secondes")
        executable = command[0]
        if "/" in executable or "\\" in executable:
            candidate = Path(executable).expanduser()
            if not candidate.is_absolute():
                candidate = config_path.parent / candidate
            executable = str(candidate.resolve())
            available = Path(executable).is_file() and os.access(executable, os.X_OK)
        else:
            found = shutil.which(executable)
            available = found is not None
            executable = found or executable
        # Resolve only the executable. Script paths must be absolute, so the
        # same configuration cannot change meaning with a request's cwd.
        result.append({"name": name, "extensions": extensions, "command": [executable, *command[1:]],
                       "output_extension": output, "timeout": timeout, "available": available,
                       "configuration_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest()})
        seen.update(extensions)
    return result


def import_capabilities():
    error = None
    try:
        converters = load_converters()
    except (ValueError, OSError) as failure:
        converters, error = [], str(failure)
    rows = []
    for extension in sorted(SOURCE_EXTENSIONS):
        adapter = next((c for c in converters if extension in c["extensions"]), None)
        conditional = extension in CONVERTER_EXTENSIONS
        kind = ("cad" if extension in CAD_EXTENSIONS else "converter" if conditional else
                "cadpack" if extension == ".cadpack" else "mesh")
        row = {"extension": extension, "kind": kind, "conditional": conditional,
               "available": not conditional or bool(adapter and adapter["available"])}
        if conditional:
            row["converter"] = adapter["name"] if adapter else None
            row["reason"] = error or ("Exécutable introuvable" if adapter and not adapter["available"] else
                                      "Convertisseur local requis" if not adapter else "Conversion locale configurée ; le logiciel doit lire cette version de fichier")
        if extension == ".cadpack":
            row["reason"] = "Conteneur d'assemblage ; dépend du convertisseur du fichier principal"
        rows.append(row)
    return {"schema": "atelier.import_capabilities.v1", "compatibility_target": "2020+ (cible, pas certification de tous les logiciels)",
            "formats": rows, "converter_configuration_error": error,
            "mesh_import_note": "Les maillages sont reconstruits par leur peau ; groupes, matériaux et conditions physiques ne sont pas transférés au nouveau maillage."}


def check_regular_source(source):
    source = Path(source)
    if source.is_symlink() or not source.is_file() or not 0 < source.stat().st_size <= MAX_SOURCE_BYTES:
        raise ValueError("La source doit être un fichier régulier non vide de 200 Mio maximum")
    safe_filename(source.name)
    return source


def archive_entries(archive):
    entries = archive.infolist()
    if len(entries) > 4096 or sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES:
        raise ValueError("Archive trop volumineuse après décompression ou contenant trop de fichiers")
    names, portable_names = set(), set()
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if (not entry.filename or entry.filename in names or path.is_absolute() or ".." in path.parts
                or "\\" in entry.filename or ":" in entry.filename or "\0" in entry.filename
                or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1):
            raise ValueError("Chemin, doublon, lien ou chiffrement interdit dans l'archive")
        if any(ord(c) < 32 for c in entry.filename):
            raise ValueError("Caractère de contrôle dans un chemin d'archive")
        portable = path.as_posix().rstrip("/").casefold()
        if portable in portable_names:
            raise ValueError("Chemins d'archive ambigus sur un système insensible à la casse")
        portable_names.add(portable)
        names.add(entry.filename)
    return {entry.filename: entry for entry in entries if not entry.is_dir()}


def create_cadpack(source, directory, output):
    """Emballer un dossier CAO explicitement choisi, sans suivre de liens."""
    directory = Path(directory).expanduser().resolve()
    source = Path(source).expanduser()
    if not source.is_absolute():
        source = directory / source
    if source.is_symlink():
        raise ValueError("La source principale ne doit pas être un lien")
    source = source.resolve()
    output = Path(output).expanduser().resolve()
    if not directory.is_dir() or not source.is_relative_to(directory):
        raise ValueError("La source doit être un fichier à l'intérieur du dossier choisi")
    check_regular_source(source)
    if source_extension(source.name) not in CAD_EXTENSIONS | CONVERTER_EXTENSIONS:
        raise ValueError("La source du paquet doit être une CAO standard ou un format avec passerelle")
    if output.suffix.lower() != ".cadpack" or output.exists():
        raise ValueError("Choisir un nouveau fichier de sortie .cadpack")
    paths, total, omitted = [], 0, 0
    for base, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink())
        for name in sorted(files):
            path = Path(base) / name
            if source_extension(name) not in CAD_EXTENSIONS | CONVERTER_EXTENSIONS:
                omitted += 1
                continue
            if path.is_symlink():
                raise ValueError("Un fichier CAO du dossier est un lien ; copier ses données avant de créer le paquet")
            check_regular_source(path)
            for part in path.relative_to(directory).parts[:-1]:
                if part.endswith((".", " ")) or part.startswith("."):
                    raise ValueError("Nom de dossier CAO non portable sur Windows")
                safe_filename(part + ".step")
            total += path.stat().st_size
            paths.append(path)
            if total > MAX_EXPANDED_BYTES or len(paths) > 4095:
                raise ValueError("Le dossier CAO dépasse 200 Mio ou 4095 fichiers")
    if source not in paths:
        raise ValueError("La source principale n'a pas été incluse")
    digests = {}
    for path in paths:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        digests[path.relative_to(directory).as_posix()] = digest.hexdigest()
    manifest = {"schema": "atelier.cadpack.v1", "source": source.relative_to(directory).as_posix(), "files": digests,
                "note": "Dossier CAO choisi explicitement ; seules les extensions CAO reconnues sont incluses. Vérifier les références avant conversion."}
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("cadpack.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for path in paths:
                archive.write(path, path.relative_to(directory).as_posix())
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return {"file": str(output), "files": len(paths), "source_bytes": total,
            "archive_bytes": output.stat().st_size, "omitted_non_cad_files": omitted}


def unpack_cadpack(source, destination):
    destination = Path(destination)
    with zipfile.ZipFile(source) as archive:
        entries = archive_entries(archive)
        if "cadpack.json" not in entries or entries["cadpack.json"].file_size > 1024 * 1024:
            raise ValueError("Le paquet doit contenir cadpack.json")
        manifest = strict_json(archive.read("cadpack.json"))
        if not isinstance(manifest, dict) or manifest.get("schema") != "atelier.cadpack.v1":
            raise ValueError("Schéma cadpack inconnu")
        main = manifest.get("source")
        if not isinstance(main, str) or main not in entries or source_extension(main) not in CONVERTER_EXTENSIONS | CAD_EXTENSIONS:
            raise ValueError("Source principale absente ou incompatible dans le paquet")
        hashes = manifest.get("files")
        if hashes is not None:
            if not isinstance(hashes, dict) or set(hashes) != set(entries) - {"cadpack.json"}:
                raise ValueError("La liste d'intégrité cadpack ne correspond pas à ses fichiers")
            for name, expected in hashes.items():
                if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
                    raise ValueError("Empreinte cadpack invalide")
                digest = hashlib.sha256()
                with archive.open(name) as file:
                    for chunk in iter(lambda: file.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != expected:
                    raise ValueError("Un fichier cadpack a été altéré")
        for name in entries:
            if name == "cadpack.json":
                continue
            safe_filename(PurePosixPath(name).name)
            for part in PurePosixPath(name).parts[:-1]:
                # Folder names also need to be portable on Windows.
                if part.endswith((".", " ")) or part.startswith("."):
                    raise ValueError("Nom de dossier cadpack non portable sur Windows")
                safe_filename(part + ".step")
            if source_extension(name) == ".cadpack":
                raise ValueError("Les paquets imbriqués ne sont pas acceptés")
        destination.mkdir(parents=True, exist_ok=False)
        for name in entries:
            if name != "cadpack.json":
                target = destination / PurePosixPath(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
    return destination / PurePosixPath(main)


def run_converter(source, directory):
    extension = source_extension(source.name)
    converter = next((c for c in load_converters() if extension in c["extensions"]), None)
    if converter is None:
        raise ValueError(f"{extension} exige un convertisseur local. Configurer MAILLEUR_CAD_CONVERTERS ou exporter la pièce en STEP depuis son logiciel d'origine. Voir docs/IMPORTS_NATIFS.md.")
    if not converter["available"]:
        raise ValueError(f"Le convertisseur {converter['name']} est configuré mais son exécutable est introuvable")
    output = directory / ("converted" + converter["output_extension"])
    if output.exists() or output.is_symlink():
        raise ValueError("La destination du convertisseur existe déjà")
    arguments = [str(source) if a == "{input}" else str(output) if a == "{output}" else a for a in converter["command"]]
    log = directory / "converter.log"
    try:
        with log.open("wb") as stream:
            process = subprocess.run(arguments, shell=False, cwd=directory, stdout=stream, stderr=subprocess.STDOUT,
                                     timeout=converter["timeout"], check=False)
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"Le convertisseur {converter['name']} a dépassé son délai") from error
    if process.returncode:
        raise ValueError(f"Échec du convertisseur {converter['name']} (code {process.returncode}) ; consulter converter.log")
    check_regular_source(output)
    if output.suffix.lower() in (".step", ".stp"):
        with output.open("rb") as stream:
            if b"ISO-10303-21" not in stream.read(4096).upper():
                raise ValueError("Le convertisseur n'a pas produit un fichier STEP reconnaissable")
    return output, {"name": converter["name"], "configuration_sha256": converter["configuration_sha256"],
                    "output_extension": output.suffix.lower(), "log": log.name,
                    "note": "Conversion locale ; la validation finale est effectuée par le moteur de maillage."}


def xml_source(payload):
    if b"\0" in payload:
        raise ValueError("XML UTF-8 attendu ; encodages avec octets nuls refusés")
    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ValueError("Les déclarations DTD et entités XML ne sont pas acceptées")
    return ET.fromstring(payload)


def _transform_3mf(value):
    if value is None:
        return []
    numbers = [float(x) for x in value.split()]
    if len(numbers) != 12 or any(not math.isfinite(x) for x in numbers):
        raise ValueError("Une transformation 3MF doit contenir 12 nombres finis")
    # File matrix uses row vectors. Rust receives the equivalent column-vector
    # affine matrix. This is a representation change, not geometry computation.
    return [[numbers[0], numbers[3], numbers[6], numbers[9],
             numbers[1], numbers[4], numbers[7], numbers[10],
             numbers[2], numbers[5], numbers[8], numbers[11], 0., 0., 0., 1.]]


def read_3mf(source, input_unit):
    namespace = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    ns = {"m": namespace}
    with zipfile.ZipFile(source) as archive:
        entries = archive_entries(archive)
        model_path = "3D/3dmodel.model"
        if "_rels/.rels" in entries:
            relationships = xml_source(archive.read("_rels/.rels"))
            starts = [r for r in relationships if r.get("Type", "").endswith("/3dmodel")]
            if len(starts) != 1 or starts[0].get("TargetMode") == "External":
                raise ValueError("Le conteneur 3MF doit désigner un modèle local unique")
            model_path = starts[0].get("Target", "").lstrip("/")
        if model_path not in entries or ".." in PurePosixPath(model_path).parts:
            raise ValueError("Modèle 3MF principal introuvable")
        model = xml_source(archive.read(model_path))
    if model.tag != "{" + namespace + "}model":
        raise ValueError("Espace de noms 3MF Core non pris en charge")
    if model.get("requiredextensions", "").strip():
        raise ValueError("Ce 3MF exige des extensions non prises en charge ; exporter une géométrie Core ou un STEP")
    unit = model.get("unit", "millimeter")
    if unit not in UNIT_3MF or input_unit not in UNIT_METRES:
        raise ValueError("Unité 3MF ou unité du projet inconnue")
    resources, build = model.find("m:resources", ns), model.find("m:build", ns)
    if resources is None or build is None:
        raise ValueError("Ressources ou instructions build absentes du 3MF")
    objects = {}
    for obj in resources.findall("m:object", ns):
        identifier = obj.get("id")
        if not identifier or identifier in objects:
            raise ValueError("Identifiant d'objet 3MF absent ou répété")
        objects[identifier] = obj
    if len(objects) > MAX_INSTANCES:
        raise ValueError("Trop d'objets 3MF")
    instances, instance_names, total_points, total_cells, resolutions = [], [], 0, 0, 0
    def resolve(identifier, parents, stack):
        nonlocal total_points, total_cells, resolutions
        resolutions += 1
        if resolutions > MAX_INSTANCES * 32:
            raise ValueError("Trop de références de composants 3MF")
        if identifier in stack or len(stack) >= 32 or len(instances) >= MAX_INSTANCES:
            raise ValueError("Cycle, profondeur ou nombre d'instances 3MF excessif")
        if identifier not in objects:
            raise ValueError("Référence 3MF vers un objet absent")
        obj = objects[identifier]
        if obj.get("type", "model") != "model":
            raise ValueError("Seuls les objets 3MF de type model sont acceptés dans build")
        mesh, components = obj.find("m:mesh", ns), obj.find("m:components", ns)
        if (mesh is None) == (components is None):
            raise ValueError("Chaque objet 3MF doit contenir un mesh ou des composants")
        if mesh is not None:
            vertices, triangles = mesh.find("m:vertices", ns), mesh.find("m:triangles", ns)
            if vertices is None or triangles is None:
                raise ValueError("Sommets ou triangles 3MF absents")
            points = [[float(v.attrib[key]) for key in ("x", "y", "z")] for v in vertices.findall("m:vertex", ns)]
            cells = [[int(t.attrib[key]) for key in ("v1", "v2", "v3")] for t in triangles.findall("m:triangle", ns)]
            total_points += len(points); total_cells += len(cells)
            if not points or not cells or total_points > MAX_POINTS or total_cells > MAX_CELLS:
                raise ValueError("Mesh 3MF vide ou trop volumineux")
            instances.append({"points": points, "cells": [{"type": "triangle", "data": cells}],
                              "transforms": parents})
            instance_names.append(obj.get("name", identifier))
        else:
            children = components.findall("m:component", ns)
            if not children:
                raise ValueError("Un assemblage 3MF ne doit pas avoir de composants vides")
            for component in children:
                if any(key.split("}")[-1] == "path" for key in component.attrib):
                    raise ValueError("Les composants 3MF externes/multifichiers ne sont pas pris en charge")
                resolve(component.get("objectid"), _transform_3mf(component.get("transform")) + parents, stack + [identifier])
    for item in build.findall("m:item", ns):
        if any(key.split("}")[-1] == "path" for key in item.attrib):
            raise ValueError("Les instances 3MF externes ne sont pas prises en charge")
        resolve(item.get("objectid"), _transform_3mf(item.get("transform")), [])
    if not instances:
        raise ValueError("Le build 3MF ne contient aucune géométrie")
    return {"schema": "atelier.mesh_import.v1", "objects": instances,
            "coordinate_scale": UNIT_3MF[unit] / UNIT_METRES[input_unit],
            "reject_overlapping_objects": True,
            "metadata": {"source_format": "3mf-core", "source_unit": unit, "project_input_unit": input_unit,
                         "instances": len(instances), "object_names": instance_names, "materials_imported": False,
                         "warnings": ["3MF Core géométrique seulement : couleurs, textures, matériaux et réglages de fabrication ne deviennent pas des données de calcul.",
                                      "Les instances qui se chevauchent ou se touchent sont refusées conservativement ; aucune union booléenne de maillages n'est inventée."]}}


def _check_external_references(source, reader):
    if reader in ("abaqus", "nastran"):
        with source.open("r", encoding="utf-8", errors="replace") as stream:
            if any(re.match(r"^\s*(?:\*INCLUDE\b|INCLUDE\b)", line, re.I) for line in stream):
                raise ValueError("Les fichiers INCLUDE externes sont refusés ; exporter un maillage autonome")
    if reader == "vtu":
        with source.open("rb") as stream:
            header = stream.read(1024 * 1024)
        if b"<!DOCTYPE" in header.upper() or b"<!ENTITY" in header.upper():
            raise ValueError("Déclarations DTD/entités interdites dans le VTU")
        for label, maximum in ((b"NumberOfPoints", MAX_POINTS), (b"NumberOfCells", MAX_CELLS)):
            for match in re.findall(label + rb'\s*=\s*[\"\'](\d+)', header):
                if int(match) > maximum:
                    raise ValueError("Le maillage VTU dépasse les limites de points ou de cellules")
    if reader == "med":
        import h5py
        with h5py.File(source, "r") as file:
            pending, count, visited = [file], 0, set()
            while pending:
                group = pending.pop()
                address = h5py.h5o.get_info(group.id).addr
                if address in visited:
                    continue
                visited.add(address)
                for key in group.keys():
                    count += 1
                    if count > 100000:
                        raise ValueError("Conteneur MED trop complexe")
                    link = group.get(key, getlink=True)
                    if isinstance(link, (h5py.ExternalLink, h5py.SoftLink)):
                        raise ValueError("Les liens HDF5 externes ou symboliques sont refusés")
                    child = group[key]
                    if isinstance(child, h5py.Group):
                        pending.append(child)
                    elif child.is_virtual or child.external or child.size > 40_000_000:
                        raise ValueError("Données MED externes, virtuelles ou trop volumineuses refusées")


def read_mesh_file(source, reader):
    import meshio
    import numpy as np
    _check_external_references(source, reader)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            mesh = meshio.read(source, file_format=reader)
    except SystemExit as error:
        raise ValueError(f"Le lecteur {reader} a refusé ce fichier ; consulter le journal d'import") from error
    points = np.asarray(mesh.points)
    count = sum(len(c.data) for c in mesh.cells)
    if (points.ndim != 2 or points.shape[1] not in (2, 3) or not 0 < len(points) <= MAX_POINTS
            or not 0 < count <= MAX_CELLS or not np.isfinite(points).all()):
        raise ValueError("Points/cellules absents, non finis ou au-delà des limites d'import")
    if points.shape[1] == 2:
        points = np.column_stack((points, np.zeros(len(points))))
    cells = []
    for cell in mesh.cells:
        if cell.data.ndim != 2 or cell.data.dtype.kind not in "iu":
            raise ValueError("Connectivité de fichier non entière ou irrégulière")
        cells.append({"type": cell.type, "data": cell.data.tolist()})
    groups = {name: sum(0 if values is None else len(values) for values in parts)
              for name, parts in mesh.cell_sets.items()}
    return {"schema": "atelier.mesh_import.v1", "points": points.tolist(), "cells": cells,
            "metadata": {"source_format": reader, "source_nodes": len(points), "source_cells": count,
                         "cell_groups": groups, "point_groups": {k: len(v) for k, v in mesh.point_sets.items()},
                         "point_data_fields": list(mesh.point_data), "cell_data_fields": list(mesh.cell_data),
                         "warnings": ["Import pour remaillage : extraction de peau ; les groupes et champs du fichier initial sont consignés mais ne sont pas transférés au nouveau maillage."]}}


def prepare_source(source, directory, input_unit="mm"):
    """Décoder une source. Retourne un fichier CAO direct ou un DTO pour Rust."""
    source = check_regular_source(source)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    extension = source_extension(source.name)
    report = {"schema": "atelier.import_preparation.v1", "source_format": extension,
              "source_bytes": source.stat().st_size, "original_preserved": True, "warnings": []}
    if extension == ".cadpack":
        source = unpack_cadpack(source, directory / "assembly")
        report["assembly_main"] = str(source.relative_to(directory))
        extension = source_extension(source.name)
    if extension in CONVERTER_EXTENSIONS:
        source, converter = run_converter(source, directory)
        report["converter"] = converter
        report["warnings"].append("Le fichier propriétaire est converti localement ; ni arbre de fonctions, ni contraintes d'assemblage ni modèle physique ne sont transférés.")
        extension = source_extension(source.name)
    if extension in CAD_EXTENSIONS | NATIVE_GMSH_EXTENSIONS:
        report.update(kind="direct", file=str(source))
    else:
        if extension == ".3mf":
            data = read_3mf(source, input_unit)
        elif extension in MESH_READERS:
            data = read_mesh_file(source, MESH_READERS[extension])
        else:
            raise ValueError("Ce format n'a pas de lecteur dans cette installation")
        dto = directory / "mesh_input.json"
        write_json(dto, data)
        report.update(kind="mesh_dto", file=str(dto), metadata=data.get("metadata", {}))
    write_json(directory / "import.json", report)
    return report
