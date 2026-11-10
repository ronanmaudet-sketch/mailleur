"""Compare the rc4/current JSON bridge on the same native executable.

This benchmark measures file/IPC overhead, not CAD or meshing throughput.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import os
import statistics
import sys
import tempfile
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", type=Path,
                        default=ROOT / "verification/optimization/reproduction/native_rc4.py")
    parser.add_argument("--output", type=Path, default=ROOT / "verification/optimization/transport.json")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--calls", type=int, default=20)
    args = parser.parse_args()
    if args.repetitions < 1 or args.calls < 1:
        parser.error("positive counts are required")
    from atelier import native
    binary = native.core_path()
    os.environ["ATELIER_CORE"] = str(binary)
    spec = importlib.util.spec_from_file_location("atelier._native_baseline", args.baseline_source)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    preview = {"points": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
               "cells": [{"type": "triangle", "data": [[0, 1, 2]]}]}
    samples = {"rc4": [], "optimized": []}
    log_bytes = {}
    expected = None
    with tempfile.TemporaryDirectory(prefix="mailleur-transport-") as folder:
        root = Path(folder)
        for repeat in range(args.repetitions):
            order = ("rc4", "optimized") if repeat % 2 == 0 else ("optimized", "rc4")
            for name in order:
                session_class = baseline.NativeSession if name == "rc4" else native.NativeSession
                session = session_class(root / f"{name}-{repeat}")
                started = time.perf_counter()
                for _ in range(args.calls):
                    result = session.query("metrics", preview=preview)
                    if expected is None: expected = result
                    if result != expected: raise AssertionError("native reports differ")
                samples[name].append(time.perf_counter() - started)
                log_bytes[name] = sum(p.stat().st_size for p in session.workspace.rglob("native.log"))
        source = root / "large-source.step"
        chunk = b"CAD benchmark input\n" * 4096
        with source.open("wb") as stream:
            while stream.tell() < 16 * 1024 * 1024:
                stream.write(chunk[:min(len(chunk), 16 * 1024 * 1024 - stream.tell())])
        checksums = {}
        for name, function in (("rc4", lambda p: hashlib.sha256(p.read_bytes()).digest()),
                               ("optimized", native.file_digest)):
            times = []
            for _ in range(args.repetitions):
                started = time.perf_counter()
                digest = function(source)
                times.append(time.perf_counter() - started)
            tracemalloc.start()
            function(source)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            checksums[name] = {"seconds": times, "median_seconds": statistics.median(times),
                               "peak_additional_python_bytes": peak, "sha256": digest.hex()}
        if checksums["rc4"]["sha256"] != checksums["optimized"]["sha256"]:
            raise AssertionError("source digests differ")
        medians = {name: statistics.median(rows) for name, rows in samples.items()}
        report = {"scope": "JSON transport on the same native executable; no meshing speed claim",
                  "binary": str(binary), "calls_per_run": args.calls, "repetitions": args.repetitions,
                  "seconds": samples, "median_seconds": medians,
                  "median_ms_per_call": {k: 1000 * v / args.calls for k, v in medians.items()},
                  "native_reports_equal": True, "log_bytes_per_run": log_bytes,
                  "source_bytes": source.stat().st_size, "source_hashing": checksums}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
