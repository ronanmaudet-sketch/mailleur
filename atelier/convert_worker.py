"""Adaptateurs de fichiers isolés : un défaut natif ne ferme pas l'interface."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import traceback

from mailleur.io import write_json
from .projects import strict_json


def main():
    request = strict_json(Path(sys.argv[1]).read_bytes())
    directory = Path(request["directory"])
    try:
        from .formats import convert_mesh, export_bundle
        if request["format"] == "bundle":
            result = export_bundle(Path(request["source"]), Path(request["target"]), planar=request["planar"])
        else:
            result = convert_mesh(Path(request["source"]), Path(request["target"]), request["format"], planar=request["planar"])
        write_json(directory / "conversion.json", result)
        return 0
    except Exception as error:
        write_json(directory / "conversion_error.json", {"error": str(error)})
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
