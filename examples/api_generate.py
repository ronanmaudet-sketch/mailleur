"""Mailler un projet depuis un script, sans interface graphique.

Depuis la racine installée :
  python examples/api_generate.py examples/structured_hexa_api.json -o output/api
"""
import argparse
import json
from pathlib import Path

from atelier.api import MeshingClient
from atelier.projects import strict_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--format", default="msh")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    with MeshingClient(args.output, timeout=args.timeout) as client:
        project = strict_json(args.project.read_bytes())
        result = client.generate(project, source=args.source)
        export = client.export(result, args.output / "export", format_name=args.format)
        print(json.dumps({"result": result.to_dict(), "export": export}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
