"""Empaqueter les sources publiques et, sur demande, l'historique local des essais."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_MANIFEST = "MANIFEST_PUBLIC.sha256"


def release_files(root):
    paths = []
    for line in (root / PUBLIC_MANIFEST).read_text(encoding="utf-8").splitlines():
        _, relative = line.split("  ", 1)
        path = root / relative
        if path.resolve() != path or not path.is_file() or not path.is_relative_to(root):
            raise ValueError(f"Entrée de manifeste invalide : {relative}")
        paths.append(path)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="chemin de l’archive produite")
    parser.add_argument("--include-history", action="store_true",
                        help="ajouter les résultats locaux de verification pour un archivage privé")
    args = parser.parse_args()
    paths = set(release_files(ROOT))
    if args.include_history:
        paths.update(path for path in (ROOT / "verification").rglob("*")
                     if path.is_file() and path.resolve() == path and path.name != ".DS_Store")
    name = "Mailleur_direct_rc10_historique.zip" if args.include_history else "Mailleur_direct_rc10_sources.zip"
    archive = args.output.expanduser().resolve() if args.output else ROOT.parent / name
    if archive.suffix.lower() != ".zip" or archive in paths:
        parser.error("Choisir un fichier ZIP de sortie distinct des sources.")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=archive.parent, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        checksums = []
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as package:
            for path in sorted(paths, key=lambda item: item.as_posix()):
                relative = path.relative_to(ROOT).as_posix()
                data = path.read_bytes()
                checksums.append(f"{hashlib.sha256(data).hexdigest()}  {relative}\n")
                entry = zipfile.ZipInfo.from_file(path, arcname="atelier_mailleur/" + relative)
                package.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED)
            package.writestr("atelier_mailleur/" + PUBLIC_MANIFEST, "".join(checksums))
        with zipfile.ZipFile(temporary) as package:
            if package.testzip() is not None:
                raise ValueError("Erreur de lecture de l’archive produite")
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({"archive": str(archive), "files": len(paths) + 1,
                      "bytes": archive.stat().st_size, "include_history": args.include_history}))


if __name__ == "__main__":
    main()
