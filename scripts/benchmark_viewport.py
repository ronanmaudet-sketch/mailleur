"""Compare Qt display/picking with a prior viewport, without generating meshes.

Example:
  QT_QPA_PLATFORM=offscreen python scripts/benchmark_viewport.py \
    --baseline verification/optimization/reproduction/viewport_rc4.py \
    --triangles 20000 --output verification/optimization/viewport_reproduced.json

The baseline is executable Python from the selected project version. This is a
GUI benchmark, not an estimate of meshing performance or solver accuracy.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("A positive integer is required")
    return number


def baseline_class(path):
    """Import the viewport source supplied by the previous project version."""
    temporary = None
    source = path.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Baseline not found: {source}")
    if source.suffix.lower() == ".zip":
        temporary = tempfile.TemporaryDirectory(prefix="atelier-viewport-baseline-")
        with zipfile.ZipFile(source) as archive:
            matches = [name for name in archive.namelist()
                       if name == "atelier/viewport.py" or name.endswith("/atelier/viewport.py")]
            if len(matches) != 1:
                temporary.cleanup()
                raise ValueError("The baseline ZIP must contain exactly one atelier/viewport.py")
            item = archive.getinfo(matches[0])
            if item.file_size > 2 * 1024 * 1024:
                temporary.cleanup()
                raise ValueError("The baseline viewport source exceeds 2 MiB")
            extracted = Path(temporary.name) / "viewport_baseline.py"
            extracted.write_bytes(archive.read(item))
            source = extracted
    elif source.suffix.lower() != ".py":
        raise ValueError("The baseline must be a Python source file or a project ZIP")
    specification = importlib.util.spec_from_file_location("atelier_viewport_baseline", source)
    if specification is None or specification.loader is None:
        if temporary:
            temporary.cleanup()
        raise ValueError("Unable to load the baseline Python source")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    if not hasattr(module, "MeshViewport"):
        if temporary:
            temporary.cleanup()
        raise ValueError("The baseline does not expose MeshViewport")
    return module.MeshViewport, temporary


def display_fixture(triangle_count):
    """Synthetic display data only; it is not a computed CAD/mesh result."""
    side = math.ceil(math.sqrt(triangle_count / 2))
    points = [[x, y, .8 * math.sin(x * .09) * math.cos(y * .09)]
              for y in range(side + 1) for x in range(side + 1)]
    faces, tags = [], []
    for y in range(side):
        for x in range(side):
            first = y * (side + 1) + x
            for triangle in ([first, first + 1, first + side + 2],
                             [first, first + side + 2, first + side + 1]):
                if len(faces) == triangle_count:
                    break
                faces.append(triangle)
                tags.append(1 + y * side + x)
        if len(faces) == triangle_count:
            break
    return {"points": points, "surface_triangles": faces, "face_tags": tags}


def median_ms(callback, repeat):
    timings = []
    for index in range(repeat):
        started = time.perf_counter()
        callback(index)
        timings.append((time.perf_counter() - started) * 1000)
    return statistics.median(timings)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", type=Path,
                        default=ROOT / "verification/optimization/reproduction/viewport_rc4.py",
                        help="previous viewport.py or full rc4 project ZIP")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "verification/optimization/viewport_reproduced.json",
                        help="JSON report destination")
    parser.add_argument("--triangles", type=positive_int, default=20000,
                        help="number of synthetic display triangles (default: 20000)")
    parser.add_argument("--width", type=positive_int, default=1000)
    parser.add_argument("--height", type=positive_int, default=760)
    parser.add_argument("--repeats", type=positive_int, default=9,
                        help="unchanged repaint samples (median)")
    parser.add_argument("--motion-repeats", type=positive_int, default=7,
                        help="rotation and pan samples (median)")
    parser.add_argument("--scale-factor", type=float,
                        help="optional Qt scale factor, set before creating QApplication")
    args = parser.parse_args(argv)
    if args.width < 380 or args.height < 300:
        parser.error("width >= 380 and height >= 300 are required by MeshViewport")
    if args.triangles > 1_000_000:
        parser.error("the display fixture is limited to 1,000,000 source triangles")
    if args.scale_factor is not None:
        if not math.isfinite(args.scale_factor) or not .5 <= args.scale_factor <= 4:
            parser.error("scale-factor must be between 0.5 and 4")
        os.environ["QT_SCALE_FACTOR"] = str(args.scale_factor)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT))

    # Import Qt after parsing so --help also works before installing GUI deps.
    import numpy as np
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from atelier.viewport import MeshViewport

    old_class, baseline_temporary = baseline_class(args.baseline)
    application = QApplication.instance() or QApplication([])
    preview = display_fixture(args.triangles)
    queries = [QPointF(args.width * (11.31 + x * 55.2) / 1000,
                      args.height * (13.47 + y * 45.3) / 760)
               for y in range(15) for x in range(18)]
    reports, images, answers, views = [], [], [], []

    def pick(view, point):
        if hasattr(view, "_pick_entity"):
            return view._pick_entity(point)
        return next((tag for polygon, tag in reversed(view.projected_faces)
                     if tag > 0 and polygon.containsPoint(point, Qt.OddEvenFill)), None)

    try:
        for name, widget_type in (("baseline", old_class), ("candidate", MeshViewport)):
            view = widget_type()
            views.append(view)
            view.resize(args.width, args.height)
            view.show()
            application.processEvents()
            started = time.perf_counter()
            view.set_preview(preview)
            image = view.grab().toImage()
            first_ms = (time.perf_counter() - started) * 1000
            images.append(image.convertToFormat(QImage.Format_RGBA8888))
            idle_ms = median_ms(lambda _: view.grab(), args.repeats)

            def rotate(_):
                view.yaw += .025
                view.grab()

            def pan(_):
                view.pan_x += 4
                view.grab()

            rotation_ms = median_ms(rotate, args.motion_repeats)
            pan_ms = median_ms(pan, args.motion_repeats)
            view.yaw, view.pitch, view.zoom = -.58, .42, 1.
            view.pan_x = view.pan_y = 0.
            view.grab()
            started = time.perf_counter()
            answers.append([pick(view, position) for position in queries])
            picking_ms = (time.perf_counter() - started) * 1000
            reports.append({"version": name, "first_preview_ms": first_ms,
                            "unchanged_repaint_median_ms": idle_ms,
                            "rotation_repaint_median_ms": rotation_ms,
                            "pan_repaint_median_ms": pan_ms,
                            "picking_total_ms": picking_ms,
                            "picking_queries": len(queries),
                            "device_pixel_ratio": view.devicePixelRatioF(),
                            "logical_size": [view.width(), view.height()],
                            "displayed_triangles": len(view.faces), "lod": view.lod})
        if reports[0]["device_pixel_ratio"] != reports[1]["device_pixel_ratio"]:
            raise RuntimeError("Baseline and candidate were rendered with different DPRs")
        if images[0].size() != images[1].size():
            raise RuntimeError("Baseline and candidate produced different physical image sizes")
        if answers[0] != answers[1]:
            mismatches = sum(a != b for a, b in zip(answers[0], answers[1]))
            raise RuntimeError(f"Picking changed at {mismatches} of {len(queries)} positions")
        arrays = [np.frombuffer(image.constBits(), dtype=np.uint8).reshape(image.height(), image.width(), 4)
                  for image in images]
        difference = np.abs(arrays[0].astype(np.int16) - arrays[1].astype(np.int16))
        compared = ("unchanged_repaint_median_ms", "rotation_repaint_median_ms",
                    "pan_repaint_median_ms", "picking_total_ms")
        report = {"schema": "atelier.viewport_benchmark.v1",
                  "scope": "Qt offscreen GUI only; synthetic display fixture, no meshing benchmark",
                  "baseline": str(args.baseline.expanduser().resolve()),
                  "points": len(preview["points"]), "triangles": len(preview["surface_triangles"]),
                  "viewport": [args.width, args.height], "runs": reports, "picking_identical": True,
                  "pixel_difference_mean": float(difference[:, :, :3].mean()),
                  "pixel_difference_max": int(difference.max()),
                  "changed_pixel_fraction": float(np.any(difference[:, :, :3] != 0, axis=2).mean()),
                  "ratios": {key: reports[0][key] / reports[1][key] for key in compared},
                  "notes": ["Ratios are indicative; timings depend on the OS, Qt platform and hardware.",
                            "The first preview includes cache construction and can be slower.",
                            "Cached image alpha compositing can cause small RGB rounding differences.",
                            "The display LOD remains enabled above the product's triangle threshold."]}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        for view in views:
            view.close()
        if baseline_temporary:
            baseline_temporary.cleanup()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
        print(f"Benchmark failed: {error}", file=sys.stderr)
        raise SystemExit(2)
