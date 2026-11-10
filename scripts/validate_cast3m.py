"""Vérifier de vrais exports et un calcul élastique dans Cast3M installé.

Usage : .venv/bin/python scripts/validate_cast3m.py
Le logiciel cible doit déjà être installé. Aucun téléchargement ni changement
de son installation n'est effectué. Les calculs et journaux restent dans
--output. Ce banc vérifie des cas définis, pas toutes les versions de Cast3M.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atelier import __version__
from atelier.native import NativeSession, core_path
from atelier.formats import _read
from mailleur.io import write_json

FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"
CAST_TYPES = {"line": "SEG2", "line3": "SEG3", "triangle": "TRI3",
              "triangle6": "TRI6", "tetra": "TET4", "tetra10": "TE10"}
REFERENCE_URLS = {
    "import_unv": "https://www-cast3m.cea.fr/index.php?notice=LIRE&page=notices_classees",
    "node_count": "https://www-cast3m.cea.fr/index.php?notice=NBNO&page=notices_classees",
    "element_count": "https://www-cast3m.cea.fr/index.php?notice=NBEL&page=notices_classees",
    "tractions": "https://www-cast3m.cea.fr/index.php?notice=FSUR&page=notices_classees",
    "linear_system": "https://www-cast3m.cea.fr/index.php?notice=RESO&page=notices_classees",
    "stress": "https://www-cast3m.cea.fr/index.php?notice=SIGM&page=notices_classees",
}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def numeric_markers(text):
    return {name: float(value.replace("D", "E").replace("d", "e"))
            for name, value in re.findall(r"^(MAILLEUR_[A-Z0-9_]+)=\s*(" + FLOAT + r")\s*$", text, re.M)}


def gibi_real(value):
    result = f"{value:.17g}"
    return result if any(character in result for character in ".eE") else result + "."


def execute_castem(executable, directory, script, timeout):
    script_path = directory / "validation.dgibi"
    script_path.write_text(script, encoding="ascii")
    log_path = directory / "cast3m.log"
    environment = os.environ.copy()
    environment.update(SCRATCHDIR=str(directory))
    command = [str(executable), "-MEM", "512Mo", "-NCPU", "1", "-NOSWAP", script_path.name]
    started = time.perf_counter()
    process = None
    try:
        with log_path.open("wb") as log:
            process = subprocess.Popen(command, cwd=directory, env=environment,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=os.name != "nt")
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                NativeSession._stop_process_tree(process, separate_group=os.name != "nt")
                raise RuntimeError(f"Cast3M a dépassé {timeout:g} secondes ; consulter {log_path}") from None
    finally:
        if process is not None:
            NativeSession._stop_process_tree(process, separate_group=os.name != "nt")
    text = log_path.read_text(encoding="utf-8", errors="replace")
    levels = [int(value) for value in re.findall(r"Niveau d.erreur:\s*(\d+)", text)]
    success = code == 0 and levels and levels[-1] == 0 and "MAILLEUR_VALIDATION_OK" in text
    return {"returncode": code, "error_levels": levels, "elapsed_seconds": time.perf_counter() - started,
            "success": bool(success), "log": log_path.name, "markers": numeric_markers(text),
            "cast3m_version": next(iter(re.findall(r"VERSION ANNUELLE\s+([\d.]+)", text)), None),
            "warnings": [line.strip() for line in text.splitlines() if "IEEE_" in line or "WARNING" in line.upper()]}


def expected_mesh(mesh_path):
    import numpy as np
    mesh, _ = _read(mesh_path)
    groups = {}
    for name, per_block in mesh.cell_sets.items():
        nodes = [cell.data[indices].ravel() for cell, indices in zip(mesh.cells, per_block) if len(indices)]
        groups[name] = {"elements": sum(len(indices) for indices in per_block),
                        "nodes": len(np.unique(np.concatenate(nodes))) if nodes else 0}
    unknown = [cell.type for cell in mesh.cells if cell.type not in CAST_TYPES]
    if unknown:
        raise ValueError("Banc non défini pour ces types : " + ", ".join(unknown))
    return {"nodes": len(mesh.points), "elements": sum(len(cell.data) for cell in mesh.cells),
            "cell_counts": {CAST_TYPES[cell.type]: len(cell.data) for cell in mesh.cells}, "groups": groups}


def validation_script(bridge, expected, *, dimension, young, traction, poisson):
    lines = [f"OPTI 'DIME' {dimension} 'ECHO' 0;"]
    if dimension == 2:
        lines.append("OPTI 'MODE' 'PLAN' 'CONT';")
    lines.extend([bridge])
    if dimension == 2:
        # LIRE UNV peut réinitialiser les options globales de calcul.
        lines.append("OPTI 'MODE' 'PLAN' 'CONT';")
    lines.extend(["LIST TAB1;",
                  "MESS 'MAILLEUR_NODES=' (NBNO (TAB1.'NOEUDS'));",
                  "MESS 'MAILLEUR_ELEMENTS=' (NBEL MAIL1);"])
    for kind in expected["cell_counts"]:
        lines.append(f"MESS 'MAILLEUR_TYPE_{kind}=' (NBEL (TAB1.'@{kind}'));")
    for index, name in enumerate(sorted(expected["groups"])):
        lines.extend([f"MESS 'MAILLEUR_G_{index:03d}=' (NBEL (TAB1.'{name}'));",
                      f"MESS 'MAILLEUR_GN_{index:03d}=' (NBNO (TAB1.'{name}_GN'));"])
    for axis in range(1, dimension + 1):
        lines.extend([f"MESS 'MAILLEUR_MIN_{axis}=' (MINI (COOR {axis} MAIL1));",
                      f"MESS 'MAILLEUR_MAX_{axis}=' (MAXI (COOR {axis} MAIL1));"])
    lines.extend([
        "DOM = TAB1.'DOMAIN';",
        "NODS = CHAN DOM 'POI1';",
        "XC = COOR 1 NODS;",
        "YC = COOR 2 NODS;",
        "GAU = XC POIN 'MINI';",
        "DRO = XC POIN 'MAXI';",
        "BAS = YC POIN 'MINI';",
        "FAD = TAB1.'BOUNDARY' ELEM 'APPU' 'STRICTEMENT' DRO;",
        "MO = MODE DOM 'MECANIQUE' 'ELASTIQUE';",
        f"MA = MATE MO 'YOUN' {gibi_real(young)} 'NU' {gibi_real(poisson)};",
        "RI = RIGI MO MA;",
        "BL1 = BLOQ 'UX' GAU;",
        "BL2 = BLOQ 'UY' BAS;",
    ])
    if dimension == 3:
        lines.extend(["ZC = COOR 3 NODS;", "ARRI = ZC POIN 'MINI';",
                      "BL3 = BLOQ 'UZ' ARRI;", "BL2 = BL2 ET BL3;"])
    vector = f"({gibi_real(traction)} 0.{' 0.' if dimension == 3 else ''})"
    lines.extend([
        f"FO = FSUR 'MASS' MO {vector} FAD;",
        "UU = RESO (RI ET BL1 ET BL2) FO;",
        "UX1 = EXCO 'UX' UU 'UX';",
        "UY1 = EXCO 'UY' UU 'UY';",
        f"UTH = ({gibi_real(traction)} / {gibi_real(young)}) * (NOMC 'UX' XC);",
        f"YTH = ({gibi_real(-poisson * traction)} / {gibi_real(young)}) * (NOMC 'UY' YC);",
        "ERRU = MAXI (UX1 - UTH) 'ABS';",
        "SG = SIGM MO MA UU;",
        "REA = REAC BL1 UU;",
        "MESS 'MAILLEUR_UERROR=' ERRU;",
        "MESS 'MAILLEUR_YERROR=' (MAXI (UY1 - YTH) 'ABS');",
        "MESS 'MAILLEUR_UMAX=' (MAXI UX1);",
        "MESS 'MAILLEUR_YMIN=' (MINI UY1);",
        "MESS 'MAILLEUR_SMIN=' (MINI SG (MOTS 'SMXX'));",
        "MESS 'MAILLEUR_SMAX=' (MAXI SG (MOTS 'SMXX'));",
        "MESS 'MAILLEUR_FORCE_X=' (SOMM (EXTR FO 'VALE' 'FX'));",
        "MESS 'MAILLEUR_REACTION_X=' (SOMM (EXTR REA 'VALE' 'FX'));",
    ])
    if dimension == 3:
        lines.extend(["UZ1 = EXCO 'UZ' UU 'UZ';",
                      f"ZTH = ({gibi_real(-poisson * traction)} / {gibi_real(young)}) * (NOMC 'UZ' ZC);",
                      "MESS 'MAILLEUR_ZERROR=' (MAXI (UZ1 - ZTH) 'ABS');",
                      "MESS 'MAILLEUR_ZMIN=' (MINI UZ1);"])
    lines.extend(["MESS 'MAILLEUR_VALIDATION_OK';", "FIN;", ""])
    return "\n".join(lines)


def check_result(run, expected, *, dimension, scale, young, traction, poisson):
    values, checks = run["markers"], []
    def compare(name, reference, *, rtol=0., atol=0.):
        actual = values.get(name)
        passed = actual is not None and abs(actual - reference) <= atol + rtol * abs(reference)
        checks.append({"marker": name, "actual": actual, "expected": reference,
                       "relative_tolerance": rtol, "absolute_tolerance": atol, "passed": bool(passed)})
    compare("MAILLEUR_NODES", expected["nodes"])
    compare("MAILLEUR_ELEMENTS", expected["elements"])
    for kind, count in expected["cell_counts"].items():
        compare("MAILLEUR_TYPE_" + kind, count)
    for index, name in enumerate(sorted(expected["groups"])):
        compare(f"MAILLEUR_G_{index:03d}", expected["groups"][name]["elements"])
        compare(f"MAILLEUR_GN_{index:03d}", expected["groups"][name]["nodes"])
    for axis, maximum in enumerate([2 * scale, scale, scale][:dimension], 1):
        compare(f"MAILLEUR_MIN_{axis}", 0., atol=1e-12 * scale)
        compare(f"MAILLEUR_MAX_{axis}", maximum, rtol=1e-9)
    displacement = traction * (2 * scale) / young
    # MESS imprime par défaut six chiffres significatifs : le maximum affiché
    # a une tolérance d'affichage ; ERRU est calculée dans Cast3M sur tous nœuds.
    compare("MAILLEUR_UMAX", displacement, rtol=2e-6)
    error_limit = abs(displacement) * 1e-8
    for axis in ["U", "Y", *( ["Z"] if dimension == 3 else [])]:
        marker = f"MAILLEUR_{axis}ERROR"
        field_error = values.get(marker)
        checks.append({"marker": marker, "actual": field_error, "maximum": error_limit,
                       "passed": field_error is not None and field_error <= error_limit})
    compare("MAILLEUR_YMIN", -poisson * displacement / 2, rtol=2e-6)
    if dimension == 3:
        compare("MAILLEUR_ZMIN", -poisson * displacement / 2, rtol=2e-6)
    compare("MAILLEUR_SMIN", traction, rtol=2e-6)
    compare("MAILLEUR_SMAX", traction, rtol=2e-6)
    resultant = traction * (scale if dimension == 2 else scale * scale)
    compare("MAILLEUR_FORCE_X", resultant, rtol=2e-6)
    compare("MAILLEUR_REACTION_X", -resultant, rtol=2e-6)
    return {"passed": run["success"] and all(check["passed"] for check in checks), "checks": checks,
            "analytical": {"young": young, "poisson": poisson, "traction": traction,
                           "length": 2 * scale, "maximum_displacement": displacement,
                           "axial_stress": traction, "force_resultant": resultant,
                           "solution": "ux=traction*x/young; uy=-nu*traction*y/young; uz=-nu*traction*z/young en volume",
                           "boundary_conditions": "ux=0 sur x=0; uy=0 sur y=0; uz=0 sur z=0 en volume; traction uniforme sur x=L, autres faces libres",
                           "plane_case": "contraintes planes, profondeur unitaire" if dimension == 2 else None}}


def run_case(output, executable, dimension, order, unit, timeout):
    name = f"{'plan' if dimension == 2 else 'volume'}_p{order}_{unit}"
    directory = output / name
    directory.mkdir(parents=True, exist_ok=True)
    scale = 1. if unit == "mm" else .001
    young, traction = (210000., 10.) if unit == "mm" else (210e9, 10e6)
    poisson = .3
    project = {"schema_version": 1, "name": "Validation Cast3M " + name,
               "mode": "planar" if dimension == 2 else "volume",
               "geometry": {"kind": "plate", "width": 2., "height": 1.} if dimension == 2 else
                           {"kind": "box", "width": 2., "height": 1., "depth": 1.},
               "units": {"input": "mm", "internal": "m"},
               "mesh": {"size": .4, "min_size": .1, "order": order, "threads": 1, "max_elements": 20000}}
    write_json(directory / "project.json", project)
    session = NativeSession(directory / "atelier", timeout=timeout)
    native = session.generate(project)
    source_directory = Path(native["directory"])
    expected = expected_mesh(source_directory / "mesh.msh")
    exports = directory / "exports"
    exports.mkdir(exist_ok=True)
    export = session.export(source_directory, exports / "import_cast3m.dgibi", "cast3m", units=unit, planar=dimension == 2)
    bridge = (exports / "import_cast3m.dgibi").read_text(encoding="ascii")
    identities = {name: digest(exports / name) for name in export["files"]}
    script = validation_script(bridge, expected, dimension=dimension, young=young,
                               traction=traction, poisson=poisson)
    castem = execute_castem(executable, exports, script, timeout)
    validation = check_result(castem, expected, dimension=dimension, scale=scale, young=young,
                              traction=traction, poisson=poisson)
    unchanged = all(digest(exports / name) == value for name, value in identities.items())
    result = {"case": name, "dimension": dimension, "order": order, "export_unit": unit,
              "export_files_unchanged_by_solver": unchanged, "source_directory": str(source_directory.relative_to(directory)),
              "expected": expected, "cast3m": castem, "validation": validation,
              "native_quality": native["report"]["quality"],
              "passed": unchanged and validation["passed"]}
    write_json(directory / "result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--castem", type=Path, default=Path("/Applications/Cast3M/bin/castem26"))
    parser.add_argument("--output", type=Path, default=ROOT / "verification" / "extended_20261107" / "cast3m")
    parser.add_argument("--timeout", type=float, default=60.)
    args = parser.parse_args(argv)
    executable, output = args.castem.expanduser().resolve(), args.output.expanduser().resolve()
    if not executable.is_file():
        parser.error("Cast3M doit être installé ; utiliser --castem pour son lanceur")
    output.mkdir(parents=True, exist_ok=True)
    kernel = executable.parent / "bin_Cast3M_Darwin_EDURE_26"
    summary = {"schema": "atelier.validation.cast3m.v1", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
               "atelier_version": __version__, "python": sys.version.split()[0],
               "machine": {"architecture": platform.machine(), "macos": platform.mac_ver()[0], "os_release": platform.release()},
               "cast3m_launcher": str(executable), "cast3m_launcher_sha256": digest(executable),
               "cast3m_binary": str(kernel) if kernel.exists() else None,
               "cast3m_binary_sha256": digest(kernel) if kernel.exists() else None,
               "cast3m_binary_format": subprocess.run(["file", str(kernel)], capture_output=True, text=True).stdout.strip() if kernel.exists() else None,
               "native_kernel_sha256": digest(core_path()),
               "native_kernel_version": subprocess.run([str(core_path()), "--version"], check=True, capture_output=True, text=True).stdout.strip(),
               "references": REFERENCE_URLS, "cases": []}
    for dimension, order, unit in [(2, 1, "mm"), (2, 2, "mm"), (3, 1, "mm"), (3, 2, "mm"), (3, 2, "m")]:
        name = f"{'plan' if dimension == 2 else 'volume'}_p{order}_{unit}"
        try:
            result = run_case(output, executable, dimension, order, unit, args.timeout)
        except Exception as error:
            result = {"case": name, "passed": False, "error": str(error), "error_type": type(error).__name__}
        summary["cases"].append(result)
        write_json(output / "summary.json", summary)
        print(json.dumps({"case": name, "passed": result["passed"], "error": result.get("error")}, ensure_ascii=False), flush=True)
    summary["passed"] = all(case["passed"] for case in summary["cases"])
    summary["scope"] = "Exécution réelle de Cast3M installé, imports UNV générés et cinq résolutions élastiques analytiques ; pas de certification de toutes versions ni de modèles industriels."
    write_json(output / "summary.json", summary)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
