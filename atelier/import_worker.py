"""Décodage de formats de fichiers dans un processus isolé et borné par l'appelant."""
from pathlib import Path
import sys
import traceback

from mailleur.io import write_json
from .projects import strict_json


def main():
    if len(sys.argv) != 2:
        return 2
    request = strict_json(Path(sys.argv[1]).read_bytes())
    directory = Path(request["directory"])
    try:
        from .imports import prepare_source
        prepare_source(Path(request["source"]), directory, request.get("input_unit", "mm"))
        return 0
    except Exception as error:
        write_json(directory / "import_error.json", {"error": str(error), "type": type(error).__name__})
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
