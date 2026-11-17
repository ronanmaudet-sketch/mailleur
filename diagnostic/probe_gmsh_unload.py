"""Compare normal Gmsh shutdown and an explicit Windows DLL unload."""
from __future__ import annotations

import argparse
import subprocess
import sys
import time


def phase(message):
    print(f"GMSH_PROBE {message}", flush=True)


def child(release_library):
    phase("before import")
    import gmsh
    phase(f"imported gmsh {gmsh.__version__}")
    gmsh.initialize([], readConfigFiles=False)
    phase("initialized")
    gmsh.option.setNumber("General.NumThreads", 1)
    gmsh.model.add("dll-lifecycle-probe")
    gmsh.model.occ.addRectangle(0, 0, 0, 1, 1)
    gmsh.model.occ.synchronize()
    phase("geometry synchronized")
    gmsh.option.setNumber("Mesh.MeshSizeMax", 0.3)
    gmsh.model.mesh.generate(2)
    phase("mesh generated")
    gmsh.finalize()
    phase("finalized")
    if release_library:
        phase("before FreeLibrary")
        import _ctypes
        _ctypes.FreeLibrary(gmsh.lib._handle)
        phase("after FreeLibrary")
    phase("child exiting")


def parent():
    failures = 0
    for mode in ("normal", "explicit-unload"):
        command = [sys.executable, __file__, "--child"]
        if mode == "explicit-unload":
            command.append("--release-library")
        phase(f"starting {mode}")
        started = time.monotonic()
        try:
            result = subprocess.run(command, capture_output=True, timeout=30)
        except subprocess.TimeoutExpired as error:
            print((error.stdout or b"").decode("utf-8", errors="replace"), flush=True)
            print((error.stderr or b"").decode("utf-8", errors="replace"), file=sys.stderr, flush=True)
            phase(f"{mode} timed out after 30 seconds")
            failures += 1
        else:
            print(result.stdout.decode("utf-8", errors="replace"), flush=True)
            print(result.stderr.decode("utf-8", errors="replace"), file=sys.stderr, flush=True)
            phase(f"{mode} return code {result.returncode}, seconds {time.monotonic()-started:.3f}")
            failures += result.returncode != 0
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--release-library", action="store_true")
    args = parser.parse_args()
    if args.child:
        child(args.release_library)
    else:
        raise SystemExit(parent())
