"""Créer l'archive source complète, sans environnements ni caches de construction."""
from pathlib import Path
import argparse
import hashlib
import json
import platform
import shutil
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def current_verification_runs(summary):
    """Keep the measured release and its data, without copying older campaigns."""
    roots = ("verification/continuous_learning/continuous_runs/",
             "verification/corpus/pilot/", "verification/corpus/sessions/")
    selected = {prefix: set() for prefix in roots}
    def visit(value):
        if isinstance(value, dict):
            for item in value.values(): visit(item)
        elif isinstance(value, list):
            for item in value: visit(item)
        elif isinstance(value, str):
            candidate = value.replace("\\", "/")
            root_prefix = ROOT.as_posix() + "/"
            if candidate.startswith(root_prefix):
                candidate = candidate[len(root_prefix):]
            for prefix in roots:
                if candidate.startswith(prefix):
                    run = candidate[len(prefix):].split("/", 1)[0]
                    if run: selected[prefix].add(prefix + run + "/")
    visit(summary)
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-history", action="store_true",
                        help="inclure également les anciennes campagnes de validation")
    parser.add_argument("--output", type=Path, help="chemin de l'archive produite")
    parser.add_argument("--include-foreign-binaries", action="store_true",
                        help="inclure aussi les binaires livrés d'autres plateformes, non recompilés ici")
    args = parser.parse_args()
    name = "mailleur-core.exe" if sys.platform == "win32" else "mailleur-core"
    source = ROOT / "rust_core" / "target" / "release" / name
    target = ROOT / "rust_core" / "bin" / (sys.platform + "-" + platform.machine().lower()) / name
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    summary = json.loads((ROOT / "verification/native_release/summary.json").read_text(encoding="utf-8"))
    selected_runs = {row["directory"].replace("\\", "/") for row in summary["examples"]}
    if summary.get("learning", {}).get("directory"):
        selected_runs.add(summary["learning"]["directory"].replace("\\", "/"))
    continuous = summary.get("continuous_learning", {})
    cases = continuous.get("cases", []) if isinstance(continuous, dict) else continuous
    for row in cases:
        if row.get("directory"):
            selected_runs.add(row["directory"].replace("\\", "/"))
    selected_history = current_verification_runs(summary)
    excluded_history = 0
    excluded_foreign_binaries = 0
    directories = ("atelier", "mailleur", "rust_core", "tests", "scripts", "docs", "examples", "verification", ".github")
    paths = [ROOT / n for n in ("README.md", "LICENSE", "NOUVEAUTES.md", "CONTRIBUTING.md", "pyproject.toml", "requirements.txt", "requirements-docs.txt", "environment-linux-arm64.yml", "Lancer.command", "Lancer_linux.sh", "Lancer_windows.bat", ".gitignore")]
    for directory in directories:
        for path in (ROOT / directory).rglob("*"):
            if not path.is_file() or path.is_symlink(): continue
            relative = path.relative_to(ROOT).as_posix()
            # A packaging receipt cannot describe the ZIP that contains itself.
            # Write it beside the archive or keep it only in the working tree.
            if relative in ("verification/local_m3/package.json", "verification/rc6_20261107/package.json",
                            "verification/extended_20261107/package.json"):
                continue
            if "__pycache__" in path.parts or path.suffix in (".pyc", ".tmp") or relative.startswith("rust_core/target/"):
                continue
            if relative.startswith("rust_core/bin/") and not args.include_foreign_binaries:
                current_platform = sys.platform + "-" + platform.machine().lower()
                if not relative.startswith("rust_core/bin/" + current_platform + "/"):
                    excluded_foreign_binaries += 1
                    continue
            if not args.include_history:
                if relative.startswith("verification/native_release/runs/") and not any(relative.startswith(run + "/") for run in selected_runs):
                    excluded_history += 1
                    continue
                if any(relative.startswith(prefix) and not any(relative.startswith(run) for run in kept)
                       for prefix, kept in selected_history.items()):
                    excluded_history += 1
                    continue
            paths.append(path)
    paths = sorted(set(paths))
    manifest = ROOT / "MANIFEST.sha256"
    manifest.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ROOT).as_posix()}\n" for p in paths), encoding="utf-8")
    archive = args.output.expanduser().resolve() if args.output else ROOT.parent / "Atelier_Mailleur_Rust_Python_rc7.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    # Publish only a complete, checked ZIP. An interrupted build must not
    # truncate an existing release or expose a partially written download.
    with tempfile.NamedTemporaryFile(dir=archive.parent, prefix=archive.name + ".", suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as out:
            for path in paths + [manifest]: out.write(path, (Path(ROOT.name) / path.relative_to(ROOT)).as_posix())
        with zipfile.ZipFile(temporary) as z:
            if z.testzip(): raise ValueError("Archive invalide")
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    local_summary = ROOT / "verification/local_m3/summary.json"
    local_validation = json.loads(local_summary.read_text(encoding="utf-8")) if local_summary.is_file() else None
    latest_summary = ROOT / "verification/extended_20261107/summary.json"
    current_validation = json.loads(latest_summary.read_text(encoding="utf-8")) if latest_summary.is_file() else None
    print(json.dumps({"archive":str(archive),"files":len(paths)+1,"bytes":archive.stat().st_size,
                      "sha256":hashlib.sha256(archive.read_bytes()).hexdigest(),
                      "historical_files_excluded":excluded_history,"include_history":args.include_history,
                      "foreign_binaries_excluded":excluded_foreign_binaries,
                      "packaging_runs_tests":False,
                      "initial_linux_validation":{"python_tests":summary["python_tests"],"rust_tests":summary["rust_tests"]},
                      "historical_m3_validation":local_validation,
                      "current_validation":current_validation},indent=2))


if __name__ == "__main__": main()
