"""Vérifier la génération automatique directe et relire les éléments écrits."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from atelier import __version__
from atelier.api import MeshingClient
from atelier.formats import read_mesh


def check(client, case, order):
    project = json.loads((ROOT / "examples" / f"direct_{case}.json").read_text(encoding="utf-8"))
    mesh = project["mesh"]
    del mesh["element_shape"]
    del mesh["order"]
    if order == 1:
        mesh["order"] = 1
    started = perf_counter()
    result = client.generate(project)
    seconds = perf_counter() - started
    report = result.report
    generation = report["generation"]
    if (generation.get("method") != "rust_direct_structured"
            or generation.get("recombination") is not False
            or generation.get("intermediate_linear_mesh") is not False
            or generation.get("direct_high_order_nodes") is not (order == 2)
            or report["quality"]["accepted"] is not True):
        raise ValueError(f"Mode de génération ou qualité inattendu pour {case}, ordre {order}")
    restored = read_mesh(result.mesh_file)
    counts = Counter()
    for block in restored.cells:
        counts[block.type] += len(block.data)
    expected = {"quad": ("quad", "quad8"), "hex": ("hexahedron", "hexahedron20")}[case][order - 1]
    allowed = {"vertex", "line" if order == 1 else "line3", "quad" if order == 1 else "quad8", expected}
    divisions = mesh["structured"]["divisions"]
    expected_count = divisions[0] * divisions[1] * (divisions[2] if case == "hex" else 1)
    if counts[expected] != expected_count or set(counts) - allowed:
        raise ValueError(f"Connectivité inattendue pour {case}, ordre {order} : {counts}")
    return {"geometry": case, "order": order, "domain_type": expected, "domain_cells": counts[expected],
            "nodes": len(restored.points), "generation_method": generation["method"],
            "direct_high_order_nodes": generation["direct_high_order_nodes"],
            "minimum_quality": report["quality"]["minimum"], "cell_counts": dict(counts),
            "generation_api_seconds": seconds, "kernel_seconds": report["timings"]["generation_seconds"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    diagnostic = {"version": __version__, "cases": [], "passed": False}
    try:
        with MeshingClient(args.output, timeout=args.timeout) as client:
            for case in ("quad", "hex"):
                for order in (1, 2):
                    diagnostic["cases"].append(check(client, case, order))
        diagnostic["passed"] = True
    except Exception as exc:
        diagnostic["error"] = str(exc)
        raise
    finally:
        (args.output / "diagnostic.json").write_text(
            json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
