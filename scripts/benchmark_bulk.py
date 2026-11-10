"""Compare full native artifacts with report-only evaluation on the same mesh."""
from pathlib import Path
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="project JSON or native request containing project")
    parser.add_argument("--output", type=Path, default=ROOT / "verification/optimization/bulk.json")
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.repetitions < 1: parser.error("positive repetitions required")
    from atelier.native import core_path, gmsh_library, native_project
    value = json.loads(args.project.read_text())
    project = native_project(value.get("project", value))
    keys = ("quality", "nodes", "elements", "cell_counts", "groups", "correspondences", "algorithms", "selection")
    modes = {"full_artifacts": False, "report_only": True}
    samples = {key: [] for key in modes}
    expected = None
    artifact_bytes = {}
    with tempfile.TemporaryDirectory(prefix="mailleur-bulk-") as folder:
        for repeat in range(args.repetitions):
            order = list(modes) if repeat % 2 == 0 else list(reversed(modes))
            for name in order:
                directory = Path(folder) / f"{name}-{repeat}"
                directory.mkdir()
                request = {"operation": "mesh", "project": project, "output_dir": str(directory),
                           "gmsh_library": str(gmsh_library()), "report_only": modes[name],
                           "response_mode": "summary"}
                source = directory / "request.json"
                source.write_text(json.dumps(request))
                started = time.perf_counter()
                run = subprocess.run([str(core_path()), "--request", str(source)],
                                     capture_output=True, text=True, env=os.environ.copy(), timeout=120)
                elapsed = time.perf_counter() - started
                if run.returncode: raise RuntimeError(run.stderr)
                report = json.loads((directory / "report.json").read_text())
                semantic = {key: report[key] for key in keys}
                if expected is None: expected = semantic
                if semantic != expected: raise AssertionError("business/quality reports differ")
                samples[name].append(elapsed)
                artifact_bytes[name] = sum((directory / f).stat().st_size for f in
                                          ("model.brep", "mesh.state.msh", "mesh.msh", "preview.json", "replay.json", "project.normalized.json")
                                          if (directory / f).is_file())
    result = {"scope": "public native persisted vs report-only mode; includes display-data serialization/diagnostics",
              "repetitions": args.repetitions, "seconds": samples,
              "median_seconds": {k: statistics.median(v) for k, v in samples.items()},
              "heavy_artifact_bytes_per_evaluation": artifact_bytes,
              "business_fields_equal": True, "elements": expected["elements"], "nodes": expected["nodes"],
              "project": project, "note": "No claim about arbitrary geometries or a million-case campaign"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
