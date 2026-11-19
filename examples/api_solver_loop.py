"""Exemple de boucle neutre vers le solveur choisi par l'utilisateur.

Démonstration de protocole uniquement (aucun calcul physique) :
  python examples/api_solver_loop.py --demo -o output/solver_demo

Avec un wrapper fourni par l'utilisateur :
  python examples/api_solver_loop.py --adapter mon_adaptateur:run -o output/solver \\
      --solver CalculiX --physics static --format inp

Le wrapper run(request_path, feedback_path) lance/attend son logiciel et écrit
le schéma décrit dans docs/API_ET_COUPLAGE_SOLVEURS.md. Il gère son timeout,
ses conditions limites et le transfert des champs nécessaires à une reprise.
"""
import argparse
import importlib
import json
from pathlib import Path

from atelier.api import INDICATOR_SCHEMA, JsonFileSolverAdapter, MeshingClient
from atelier.projects import strict_json
from mailleur.io import write_json


def protocol_demo(request_path, feedback_path):
    """Fixture déterministe de transport, sans estimation d'erreur physique."""
    request = strict_json(request_path.read_bytes())
    converged = request["iteration"] >= 1
    write_json(feedback_path, {"schema": INDICATOR_SCHEMA,
        "solver": request["solver"], "physics": request["physics"],
        "units": request["units"], "mesh_id": request["mesh_id"],
        "converged": converged,
        "regions": [] if converged else [
            {"center": [2, 1.5, 1], "radius": 1, "error": .2, "target_size": .5}],
        "note": "Fixture de protocole : aucune résolution physique ni convergence démontrée"})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="indicateurs fictifs ; aucun solveur exécuté")
    mode.add_argument("--adapter", help="fonction utilisateur MODULE:FUNCTION(request_path,feedback_path)")
    parser.add_argument("--project", type=Path, default=Path(__file__).with_name("structured_hexa_api.json"))
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--solver", default="protocol-demo-only")
    parser.add_argument("--physics", default="static")
    parser.add_argument("--format", default="msh")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--max-seconds", type=float, default=300)
    args = parser.parse_args()
    if args.demo:
        runner = protocol_demo
    else:
        module, separator, function = args.adapter.partition(":")
        if not separator or not module or not function:
            parser.error("--adapter attend MODULE:FUNCTION")
        runner = getattr(importlib.import_module(module), function)
        if not callable(runner):
            parser.error("L'adaptateur doit être appelable")
    with MeshingClient(args.output, timeout=args.max_seconds) as client:
        result = client.adapt_loop(strict_json(args.project.read_bytes()), JsonFileSolverAdapter(runner),
            solver_name=args.solver, physics=args.physics, format_name=args.format,
            max_iterations=args.max_iterations, max_seconds=args.max_seconds)
        if args.demo:
            result["demonstration_only"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
