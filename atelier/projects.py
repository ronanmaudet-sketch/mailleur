"""Projets portables et exemples ; aucun script CAO n'est exécuté à l'import."""
from __future__ import annotations

import copy
import io
import json
import math
from pathlib import Path, PurePosixPath
import stat
import zipfile

from .import_registry import SOURCE_EXTENSIONS, source_extension

MAX_UPLOAD = 50 * 1024 * 1024
CAD_EXTENSIONS = SOURCE_EXTENSIONS


def _constant(value):
    raise ValueError(f"Nombre JSON non fini interdit : {value}")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Clé JSON répétée : {key}")
        result[key] = value
    return result


def strict_json(data: str | bytes):
    result = json.loads(data, parse_constant=_constant, object_pairs_hook=_object)
    def check(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Nombre JSON non fini interdit")
        if isinstance(value, dict):
            for item in value.values(): check(item)
        if isinstance(value, list):
            for item in value: check(item)
    check(result)
    return result


def safe_filename(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 180 or any(c in name for c in "/\\\0"):
        raise ValueError("Nom de fichier invalide")
    if any(ord(c) < 32 or ord(c) == 127 or c in '<>:"|?*' for c in name) or name.endswith((".", " ")):
        raise ValueError("Nom de fichier incompatible avec les systèmes pris en charge")
    if name in (".", "..") or name.startswith("."):
        raise ValueError("Nom de fichier invalide")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if name.split(".", 1)[0].upper() in reserved:
        raise ValueError("Nom de fichier réservé par Windows")
    if source_extension(name) not in CAD_EXTENSIONS:
        raise ValueError("Format de source inconnu. Consulter atelier formats ; les formats propriétaires exigent un convertisseur configuré et les scripts ne sont pas acceptés.")
    return name


def normalize_project(project: dict) -> dict:
    if not isinstance(project, dict):
        raise ValueError("Le projet doit être un objet JSON")
    try:
        data = strict_json(json.dumps(project, allow_nan=False))
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Projet JSON invalide : {error}") from error
    if data.get("schema_version", 1) != 1 or isinstance(data.get("schema_version"), bool):
        raise ValueError("Version de projet incompatible")
    data["schema_version"] = 1
    name = data.get("name", "Nouvelle pièce")
    if not isinstance(name, str) or not name.strip() or len(name) > 160:
        raise ValueError("Le projet doit avoir un nom de 1 à 160 caractères")
    data["name"] = name.strip()
    if data.get("mode", "planar") not in ("planar", "surface", "volume"):
        raise ValueError("Choisir un mode planar, surface ou volume")
    data.setdefault("mode", "planar")
    geometry = data.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("kind") not in ("plate", "polygon", "box", "sphere", "bracket", "import"):
        raise ValueError("Géométrie de projet inconnue")
    # Les chemins d'import sont gérés exclusivement par le serveur ou la CLI.
    if any(key in geometry for key in ("path", "source", "filename", "file", "script")):
        raise ValueError("La géométrie ne doit pas contenir de chemin de fichier ; joindre la source au projet")
    mesh = data.setdefault("mesh", {})
    repair = data.setdefault("repair", {})
    if not isinstance(mesh, dict) or not isinstance(repair, dict):
        raise ValueError("mesh et repair doivent être des objets JSON")
    defaults = {"size": .5, "min_size": .1, "order": 1, "algorithm": "automatic", "recombine": False, "threads": 1, "max_elements": 200000}
    for key, value in defaults.items(): mesh.setdefault(key, value)
    for key in ("size", "min_size"):
        value = mesh[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} doit être un nombre fini strictement positif")
    if mesh["min_size"] > mesh["size"]:
        raise ValueError("La taille minimale doit être inférieure ou égale à la taille globale")
    if type(mesh["order"]) is not int or mesh["order"] not in (1, 2):
        raise ValueError("L'ordre des éléments doit être 1 ou 2")
    for key, maximum in (("threads", 32), ("max_elements", 2000000)):
        if type(mesh[key]) is not int or not 1 <= mesh[key] <= maximum:
            raise ValueError(f"{key} doit être un entier de 1 à {maximum}")
    if mesh["algorithm"] not in ("automatic", "delaunay", "frontal"):
        raise ValueError("Algorithme inconnu")
    if type(mesh["recombine"]) is not bool:
        raise ValueError("recombine doit être vrai ou faux")
    for key, default, maximum in (("smoothing", 1, 100), ("curvature_elements", 16, 100)):
        if key in mesh and (type(mesh[key]) is not int or not 0 <= mesh[key] <= maximum):
            raise ValueError(f"mesh.{key} doit être un entier de 0 à {maximum}")
    if "optimize" in mesh and type(mesh["optimize"]) is not bool:
        raise ValueError("mesh.optimize doit être vrai ou faux")
    _normalize_learning_controls(data)
    for key, value in {"enabled": False, "tolerance": 1e-7, "sew": True, "small_edges": True, "small_faces": False, "make_solids": False}.items():
        repair.setdefault(key, value)
    for key in ("enabled", "sew", "small_edges", "small_faces", "make_solids"):
        if type(repair[key]) is not bool:
            raise ValueError(f"repair.{key} doit être vrai ou faux")
    tolerance = repair["tolerance"]
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("La tolérance de réparation doit être finie et strictement positive")
    return data


def _normalize_learning_controls(data):
    """Validate portable controls; all meshing and quality calculations stay in Rust."""
    def real(value, label, minimum=0, maximum=None, positive=False):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < minimum
                or (positive and value <= 0) or (maximum is not None and value > maximum)):
            raise ValueError(f"{label} : nombre fini dans les limites attendues")
        return value

    if "quality_policy" in data:
        policy = data["quality_policy"]
        if not isinstance(policy, dict):
            raise ValueError("quality_policy doit être un objet")
        for key, default in (("minimum_quality", .05), ("poor_threshold", .2)):
            policy.setdefault(key, default)
            real(policy[key], f"quality_policy.{key}", maximum=1, positive=key == "poor_threshold")
        policy.setdefault("strict", True)
        if type(policy["strict"]) is not bool:
            raise ValueError("quality_policy.strict doit être vrai ou faux")
        if "max_poor_fraction" in policy:
            real(policy["max_poor_fraction"], "max_poor_fraction", maximum=1)
        if "max_poor_elements" in policy and (type(policy["max_poor_elements"]) is not int
                                                  or not 0 <= policy["max_poor_elements"] <= 2_000_000):
            raise ValueError("max_poor_elements doit être un entier de 0 à 2000000")
    if "critical_zones" in data:
        zones = data["critical_zones"]
        if not isinstance(zones, list) or len(zones) > 128:
            raise ValueError("critical_zones doit contenir au plus 128 zones")
        names = set()
        for zone in zones:
            if not isinstance(zone, dict):
                raise ValueError("Chaque zone critique doit être un objet")
            name = zone.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 160 or name in names:
                raise ValueError("Chaque zone doit avoir un nom unique de 1 à 160 caractères")
            names.add(name)
            if zone.get("kind", "custom") not in ("contact", "interface", "stress", "custom"):
                raise ValueError("Type de zone critique inconnu")
            top_dimension = 3 if data["mode"] == "volume" else 2
            if type(zone.get("dimension")) is not int or not 0 <= zone["dimension"] <= top_dimension:
                raise ValueError(f"La dimension d'une zone doit être comprise entre 0 et {top_dimension}")
            tags = zone.get("tags")
            if (not isinstance(tags, list) or not tags or len(tags) > 2000
                    or any(type(tag) is not int or not 1 <= tag <= 2_147_483_647 for tag in tags)
                    or len(set(tags)) != len(tags)):
                raise ValueError("Une zone doit désigner des entités distinctes de tags entiers positifs")
            zone.setdefault("kind", "custom")
            zone.setdefault("buffer", 0.)
            zone.setdefault("minimum_quality", .35)
            real(zone["buffer"], "zone.buffer")
            real(zone["minimum_quality"], "zone.minimum_quality", maximum=1)
    if "refinement_regions" in data:
        regions = data["refinement_regions"]
        if not isinstance(regions, list) or len(regions) > 256:
            raise ValueError("refinement_regions doit contenir au plus 256 régions")
        for region in regions:
            if not isinstance(region, dict) or not isinstance(region.get("center"), list) or len(region["center"]) != 3:
                raise ValueError("Une région de raffinement doit avoir un centre [x,y,z]")
            for coordinate in region["center"]:
                real(coordinate, "centre", minimum=-1e100, maximum=1e100)
            real(region.get("radius"), "radius", positive=True)
            real(region.get("size"), "size", positive=True)
            if region["size"] < data["mesh"]["min_size"]:
                raise ValueError("Une taille locale ne peut pas être inférieure à min_size")
    if "learning" in data:
        learning = data["learning"]
        if not isinstance(learning, dict):
            raise ValueError("learning doit être un objet")
        for key, default, maximum in (("max_trials", 12, 64), ("epochs", 100, 200), ("max_seconds", 240, 86400)):
            learning.setdefault(key, default)
            if type(learning[key]) is not int or not 1 <= learning[key] <= maximum:
                raise ValueError(f"learning.{key} doit être un entier de 1 à {maximum}")
        for key, default in (("use_memory", True), ("enabled", False)):
            learning.setdefault(key, default)
            if type(learning[key]) is not bool:
                raise ValueError(f"learning.{key} doit être vrai ou faux")


def default_examples() -> list[dict]:
    base = {"schema_version": 1, "name": "Plaque percée", "mode": "planar",
            "geometry": {"kind": "plate", "width": 10, "height": 6, "thickness": 2, "corner_radius": .5,
                         "chamfer": 0, "holes": [{"x": 3, "y": 3, "radius": .8}, {"x": 7, "y": 3, "radius": 1}]},
            "mesh": {"size": .65, "min_size": .18, "order": 1, "algorithm": "automatic", "recombine": False, "threads": 1, "max_elements": 200000},
            "repair": {"enabled": False, "tolerance": 1e-7, "sew": True, "small_edges": True, "small_faces": False, "make_solids": False}}
    cases = []
    def add(identifier, name, description, project):
        project["name"] = name
        cases.append({"id": identifier, "name": name, "description": description, "project": normalize_project(project)})
    add("plate", "Plaque percée à congés", "Deux trous circulaires et quatre coins arrondis.", copy.deepcopy(base))
    chamfer = copy.deepcopy(base)
    chamfer["geometry"].update(corner_radius=0, chamfer=.6)
    add("chamfer", "Plaque chanfreinée", "Contour à chanfreins et trous conservés.", chamfer)
    solid = copy.deepcopy(base)
    solid["mode"] = "volume"
    solid["mesh"]["size"] = .8
    solid["repair"]["make_solids"] = True
    add("solid", "Plaque 3D percée", "Volume extrudé, trous traversants et congés du contour.", solid)
    bracket = copy.deepcopy(base)
    bracket["mode"] = "volume"
    bracket["geometry"] = {"kind": "bracket", "width": 6, "height": 5, "depth": 3, "wall": 1.5, "throughhole_radius": .4}
    bracket["mesh"].update(size=.7, min_size=.2)
    add("bracket", "Équerre 3D", "Assemblage booléen et perçage traversant.", bracket)
    shell = copy.deepcopy(base)
    shell["mode"] = "surface"
    shell["geometry"] = {"kind": "sphere", "radius": 3}
    add("shell", "Coque sphérique", "Maillage de surface courbe dans l'espace.", shell)
    polygon = copy.deepcopy(base)
    polygon["geometry"] = {"kind": "polygon", "outer": [[0, 0], [8, 0], [8, 3], [4, 3], [4, 7], [0, 7]],
                           "holes": [[[1, 1], [2.5, 1], [2.5, 2.5], [1, 2.5]]]}
    add("polygon", "Contour concave percé", "Contour polygonal avec un évidement intérieur.", polygon)
    return cases


def save_portable_project(project: dict, source: Path | None = None) -> bytes:
    project = normalize_project(project)
    if project["geometry"]["kind"] == "import" and source is None:
        raise ValueError("Joindre la source CAO au projet importé")
    manifest = {"schema": "atelier.portable.v1", "project": project, "source": None}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        if source is not None:
            source = Path(source)
            name = safe_filename(source.name)
            if not source.is_file() or source.is_symlink() or source.stat().st_size > MAX_UPLOAD:
                raise ValueError("Source absente, lien symbolique ou fichier trop volumineux")
            manifest["source"] = "source/" + name
            archive.write(source, manifest["source"])
        archive.writestr("project.json", json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2))
    return stream.getvalue()


def load_portable_project(data: bytes, destination: Path) -> dict:
    if len(data) > MAX_UPLOAD + 1024 * 1024:
        raise ValueError("Le projet dépasse la limite de 50 Mio")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2 or sum(x.file_size for x in entries) > MAX_UPLOAD + 1024 * 1024:
                raise ValueError("Archive de projet trop volumineuse ou contenant des fichiers supplémentaires")
            names = [x.filename for x in entries]
            if len(set(names)) != len(names) or "project.json" not in names:
                raise ValueError("Archive de projet invalide")
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in entry.filename or stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError("Chemin ou lien interdit dans le projet")
                if entry.flag_bits & 1:
                    raise ValueError("Les archives chiffrées ne sont pas acceptées")
            manifest = strict_json(archive.read("project.json"))
            if not isinstance(manifest, dict) or manifest.get("schema") != "atelier.portable.v1":
                raise ValueError("Format de projet portable inconnu")
            project = normalize_project(manifest["project"])
            source_name = manifest.get("source")
            source = None
            expected_names = {"project.json"}
            if source_name is not None:
                if not isinstance(source_name, str) or not source_name.startswith("source/"):
                    raise ValueError("Source de projet invalide")
                filename = safe_filename(source_name.removeprefix("source/"))
                expected_names.add(source_name)
                if set(names) != expected_names:
                    raise ValueError("Source absente ou archive contenant des fichiers supplémentaires")
                payload = archive.read(source_name)
                if len(payload) > MAX_UPLOAD:
                    raise ValueError("Source trop volumineuse")
                destination = Path(destination)
                destination.mkdir(parents=True, exist_ok=True)
                source = destination / filename
                if source.exists() or source.is_symlink():
                    raise ValueError("Le fichier de destination existe déjà ; choisir un dossier vide")
                source.write_bytes(payload)
            elif set(names) != expected_names:
                raise ValueError("Archive contenant des fichiers supplémentaires")
            if project["geometry"]["kind"] == "import" and source is None:
                raise ValueError("La source CAO manque dans ce projet importé")
            return {"project": project, "source": source}
    except (zipfile.BadZipFile, KeyError, UnicodeError, TypeError) as error:
        raise ValueError(f"Projet portable invalide : {error}") from error
