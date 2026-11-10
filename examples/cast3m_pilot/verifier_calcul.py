#!/usr/bin/env python3
"""Exécuter Cast3M et vérifier cette éprouvette, avec la bibliothèque standard.

SPDX-License-Identifier: GPL-3.0-or-later
Projet Mailleur. Adaptation de scripts/validate_cast3m.py (cas volume_p2_mm).
Ce script ne nécessite pas Mailleur pour relire un dossier déjà généré.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import tempfile
import time

GROUPS = ["BOUNDARY", "DOMAIN", "FACE_001", "FACE_002", "FACE_003", "FACE_004",
          "FACE_005", "FACE_006", "GEOMETRY_1D", "GEOMETRY_2D", "PART_001"]
FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def executable(value):
    located = shutil.which(value)
    path = Path(located or value).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError("Lanceur Cast3M introuvable ; préciser --castem")
    return path


def stop(process):
    if os.name == "nt":
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="dossier produit par generer_maillage.py")
    parser.add_argument("--castem", default="castem26", help="lanceur installé, chemin ou nom dans PATH")
    parser.add_argument("--timeout", type=float, default=60.)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("Le délai doit être positif et fini")
    launcher = executable(args.castem)
    directory = args.directory.expanduser().resolve()
    metadata = json.loads((directory / "maillage.json").read_text(encoding="utf-8"))
    if metadata["schema"] != "mailleur.cast3m.example.mesh.v1" or metadata["units"]["export"] != "mm":
        raise ValueError("Cet exemple exige les métadonnées et l'export en millimètres fournis")
    # Ne suivre que les noms de fichiers attendus par cet exemple public.
    names = ["project.json", "import_cast3m.dgibi", "import_cast3m.unv",
             "import_cast3m.dgibi.units.json", "traction.dgibi"]
    if set(metadata["files_sha256"]) != set(names):
        raise ValueError("Liste de fichiers inattendue")
    for name in names:
        if sha256(directory / name) != metadata["files_sha256"][name]:
            raise ValueError("Fichier d'entrée modifié : " + name)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="cast3m-pilot-") as scratch:
        scratch = Path(scratch)
        for name in names:
            shutil.copyfile(directory / name, scratch / name)
        env = os.environ.copy()
        env["SCRATCHDIR"] = str(scratch)
        # Le calcul s'exécute avec les fichiers relatifs dans un dossier
        # temporaire ; les traces du lanceur ne contaminent pas l'exemple.
        command = [str(launcher), "-MEM", "512Mo", "-NCPU", "1", "-NOSWAP", "traction.dgibi"]
        process = subprocess.Popen(command, cwd=scratch, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
        try:
            raw, _ = process.communicate(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            stop(process)
            raise RuntimeError("Délai de calcul Cast3M dépassé") from None
        finally:
            stop(process)
    elapsed = time.perf_counter() - started
    text = raw.decode("utf-8", errors="replace")
    levels = [int(value) for value in re.findall(r"Niveau d.erreur:\s*(\d+)", text)]
    markers = {name: float(value.replace("d", "e").replace("D", "E"))
        for name, value in re.findall(r"^(MAILLEUR_[A-Z0-9_]+)=\s*(" + FLOAT + r")\s*$", text, re.M)}
    checks = []
    def compare(name, expected, rtol=0., atol=0.):
        actual = markers.get(name)
        checks.append({"marker": name, "actual": actual, "expected": expected,
            "relative_tolerance": rtol, "absolute_tolerance": atol,
            "passed": actual is not None and abs(actual - expected) <= atol + rtol * abs(expected)})
    expected = metadata["expected"]
    compare("MAILLEUR_NODES", expected["nodes"])
    compare("MAILLEUR_ELEMENTS", expected["elements"])
    for kind in ("SEG3", "TRI6", "TE10"):
        compare("MAILLEUR_TYPE_" + kind, expected["cell_counts"][kind])
    for index, name in enumerate(GROUPS):
        compare(f"MAILLEUR_G_{index:03d}", expected["groups"][name]["elements"])
        compare(f"MAILLEUR_GN_{index:03d}", expected["groups"][name]["nodes"])
    for axis, maximum in enumerate((2., 1., 1.), 1):
        compare(f"MAILLEUR_MIN_{axis}", 0., atol=1e-12)
        compare(f"MAILLEUR_MAX_{axis}", maximum, rtol=1e-9)
    umax = 10. * 2. / 210000.
    compare("MAILLEUR_UMAX", umax, rtol=2e-6)
    for axis in ("U", "Y", "Z"):
        name = "MAILLEUR_" + axis + "ERROR"
        actual, limit = markers.get(name), abs(umax) * 1e-8
        checks.append({"marker": name, "actual": actual, "maximum": limit,
                       "passed": actual is not None and 0 <= actual <= limit})
    for name in ("MAILLEUR_YMIN", "MAILLEUR_ZMIN"):
        compare(name, -.3 * umax / 2., rtol=2e-6)
    for name in ("MAILLEUR_SMIN", "MAILLEUR_SMAX", "MAILLEUR_FORCE_X"):
        compare(name, 10., rtol=2e-6)
    compare("MAILLEUR_REACTION_X", -10., rtol=2e-6)
    version = next(iter(re.findall(r"VERSION ANNUELLE\s+([\d.]+)", text)), None)
    # Publier un extrait explicite, sans nom d'utilisateur ni chemin local.
    excerpt = ["Extrait du journal Cast3M : version, marqueurs, niveau d'erreur et indicateurs IEEE.",
               "Les en-têtes personnels du lanceur et les traces de session ne sont pas publiés.",
               "VERSION ANNUELLE " + (version or "non identifiée")]
    excerpt.extend(line.strip() for line in text.splitlines() if line.startswith("MAILLEUR_"))
    excerpt.extend("Niveau d'erreur: " + str(level) for level in levels)
    ieee = sorted(set(re.findall(r"IEEE_[A-Z_]+", text)))
    excerpt.extend("Indicateur Fortran: " + item for item in ieee)
    (directory / "cast3m_extrait.log").write_text("\n".join(excerpt) + "\n", encoding="utf-8")
    binary = None
    match = re.search(r"EXECUTION de ([A-Za-z0-9_.-]+)", text)
    if match:
        candidate = launcher.parent / match[1]
        if candidate.is_file():
            binary = {"name": candidate.name, "sha256": sha256(candidate)}
            file_command = shutil.which("file")
            if file_command:
                binary["format"] = subprocess.run([file_command, "-b", str(candidate)],
                    capture_output=True, text=True, check=True).stdout.strip()
    unchanged = all(sha256(directory / name) == metadata["files_sha256"][name] for name in names)
    success = (process.returncode == 0 and bool(levels) and levels[-1] == 0
        and "MAILLEUR_VALIDATION_OK" in text and all(check["passed"] for check in checks) and unchanged)
    result = {"schema": "mailleur.cast3m.example.result.v1", "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": success, "checks": checks, "check_count": len(checks), "input_files_unchanged": unchanged,
        "cast3m": {"version": version, "launcher_name": launcher.name, "launcher_sha256": sha256(launcher),
                   "binary": binary, "returncode": process.returncode, "error_levels": levels,
                   "fortran_flags": ieee, "elapsed_seconds": elapsed},
        "machine": {"os": platform.system(), "os_version": platform.mac_ver()[0] if platform.system() == "Darwin" else platform.release(),
                    "architecture": platform.machine()},
        "analytical": {"units": "mm / N / N/mm2", "young": 210000., "poisson": .3, "traction": 10.,
            "maximum_axial_displacement": umax, "maximum_transverse_contraction": .3 * umax / 2.,
            "applied_force": 10., "reaction_force": -10.},
        "input_files_sha256": metadata["files_sha256"],
        "public_log": {"file": "cast3m_extrait.log", "sha256": sha256(directory / "cast3m_extrait.log"), "complete_log": False},
        "scope": "Un seul cas élastique volumique quadratique ; aucune certification de toutes versions ou formulations."}
    (directory / "resultat_cast3m.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": success, "checks": len(checks), "cast3m_version": version,
                      "error_levels": levels, "elapsed_seconds": round(elapsed, 4)}, ensure_ascii=False))
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
