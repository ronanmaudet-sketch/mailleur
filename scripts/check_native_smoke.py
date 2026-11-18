"""Generate one small mesh through the public bridge and retain its diagnostics."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import time
from copy import deepcopy

import meshio


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from atelier.native import NativeSession, core_path, gmsh_library


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args(argv)
    if args.timeout < 1:
        parser.error("--timeout must be positive")

    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    session = NativeSession(output / "workspace", timeout=args.timeout)
    started = time.monotonic()
    diagnostic = {
        "schema": "atelier.native_smoke.v1",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "timeout_seconds": args.timeout,
        "passed": False,
    }
    try:
        binary = core_path()
        diagnostic["native_binary"] = str(binary)
        diagnostic["native_binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
        diagnostic["gmsh_library"] = str(gmsh_library())
        diagnostic["gmsh_package_version"] = importlib.metadata.version("gmsh")
        project = {
            "schema_version": 1,
            "name": "Native smoke check",
            "mode": "planar",
            "units": {"input": "mm", "internal": "m"},
            "geometry": {"kind": "plate", "width": 2, "height": 1},
            "mesh": {
                "size": 0.4,
                "min_size": 0.2,
                "order": 1,
                "algorithm": "automatic",
                "threads": 1,
                "max_elements": 1000,
                "recombine": False,
            },
        }
        diagnostic["cases"] = []
        for mode in ("planar", "volume"):
            for order in (1, 2):
                case = deepcopy(project)
                case["mode"] = mode
                case["mesh"].update(order=order, max_elements=10000,
                                    element_policy="quad_hex_only")
                if mode == "volume":
                    case["geometry"] = {"kind": "box", "width": 2, "height": 1, "depth": 1}
                result = session.generate(case)
                report = result["report"]
                mesh = Path(result["directory"]) / "mesh.msh"
                evidence = report.get("element_policy", {})
                if (report["nodes"] <= 0 or report["elements"] <= 0
                        or report["quality"]["minimum"] <= 0 or not mesh.is_file()
                        or evidence.get("verified") is not True
                        or evidence.get("requested") != "quad_hex_only"):
                    raise RuntimeError("The native mesh has no verified quad/hex policy or valid quality")
                restored = meshio.read(mesh)
                counts = {}
                for block in restored.cells:
                    counts[block.type] = counts.get(block.type, 0) + len(block.data)
                domain = ("hexahedron" if order == 1 else "hexahedron20") if mode == "volume" else ("quad" if order == 1 else "quad8")
                allowed = {"vertex", "line", "line3", "quad", "quad8", "quad9"}
                if mode == "volume":
                    allowed |= {"hexahedron", "hexahedron20", "hexahedron27"}
                if not counts.get(domain) or any(n and kind not in allowed for kind, n in counts.items()):
                    raise RuntimeError(f"The written mesh violates the quad/hex policy: {counts}")
                diagnostic["cases"].append({
                    "mode": mode, "order": order, "passed": True,
                    "nodes": report["nodes"], "elements": report["elements"],
                    "minimum_quality": report["quality"]["minimum"],
                    "cell_counts_readback": counts, "element_policy": evidence,
                    "mesh_file": str(mesh),
                })
        diagnostic["passed"] = True
    except Exception as error:
        diagnostic["error_type"] = type(error).__name__
        diagnostic["error"] = str(error)
    finally:
        session.cancel()
        diagnostic["elapsed_seconds"] = time.monotonic() - started
        if session.last_attempt_directory is not None:
            diagnostic["native_log"] = str(session.last_attempt_directory / "native.log")
        (output / "diagnostic.json").write_text(
            json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # ASCII escaping keeps the command usable in a legacy Windows console.
        print(json.dumps(diagnostic, ensure_ascii=True, indent=2), flush=True)
    return 0 if diagnostic["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
