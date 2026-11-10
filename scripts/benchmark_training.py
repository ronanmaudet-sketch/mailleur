"""Comparer l'entraînement Rust actuel à une source de référence.

Exemple (Rust/Cargo requis) :
  python scripts/benchmark_training.py --baseline-root Atelier_Mailleur_rc4.zip \\
      --output verification/performance/training.json --offline

Les enregistrements sont synthétiques : aucun calcul CAO ou maillage n'est
effectué. Deux exécutables Rust sont construits avec les mêmes dépendances et
le même profil release. Le rapport compare les poids et les métriques exacts.
Par défaut, données et exécutables temporaires sont supprimés à la fin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
FEATURES = 128
GENERATOR = "atelier.synthetic_training_benchmark.v1"
HARNESS = '''#![recursion_limit = "256"]
#[path = "../modules/{variant}.rs"]
mod dataset_model;
fn main() {{
    let args: Vec<String> = std::env::args().collect();
    let request: serde_json::Value = serde_json::from_slice(
        &std::fs::read(&args[1]).expect("request file")
    ).expect("request JSON");
    match dataset_model::train(&request) {{
        Ok(report) => println!("{{}}", report),
        Err(error) => {{ eprintln!("{{}}", error); std::process::exit(1); }}
    }}
}}
'''


def positive(value):
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("Un entier strictement positif est requis")
    return n


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def source_files(path):
    """Return the numerical module and crate metadata, from a directory or ZIP."""
    path = Path(path).expanduser().resolve()
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            matches = [name for name in archive.namelist()
                       if name.endswith("rust_core/src/dataset_model.rs")]
            if len(matches) != 1:
                raise ValueError("L'archive doit contenir une seule source rust_core/src/dataset_model.rs")
            prefix = matches[0].rsplit("/src/", 1)[0]
            names = {"source": matches[0], "manifest": prefix + "/Cargo.toml",
                     "lock": prefix + "/Cargo.lock"}
            result = {}
            for key, name in names.items():
                if archive.getinfo(name).file_size > 8 * 1024 * 1024:
                    raise ValueError("Source ou métadonnées de compilation trop volumineuses")
                result[key] = archive.read(name)
            return result
    candidates = [path / "rust_core", path, path / "atelier_mailleur/rust_core"]
    crate = next((p for p in candidates if (p / "src/dataset_model.rs").is_file()), None)
    if crate is None:
        raise ValueError("Source de référence introuvable : préciser le projet, rust_core ou son archive ZIP")
    return {"source": (crate / "src/dataset_model.rs").read_bytes(),
            "manifest": (crate / "Cargo.toml").read_bytes(),
            "lock": (crate / "Cargo.lock").read_bytes()}


def toml_value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(map(toml_value, value)) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {toml_value(v)}" for k, v in value.items()) + " }"
    raise ValueError("Valeur TOML de compilation non prise en charge")


def command(args, *, cwd=None, timeout=None):
    result = subprocess.run([str(x) for x in args], cwd=cwd, capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Commande échouée ({result.returncode}) :\n{result.stderr[-8000:]}")
    return result


def build_harness(work, baseline, current, cargo, offline):
    crate = work / "harness"
    (crate / "src/bin").mkdir(parents=True)
    (crate / "src/modules").mkdir()
    manifest = tomllib.loads(current["manifest"].decode("utf-8"))
    dependencies = manifest["dependencies"]
    profile = manifest.get("profile", {}).get("release", {})
    text = '[package]\nname = "atelier-training-benchmark"\nversion = "0.1.0"\nedition = "2021"\n'
    text += "\n[dependencies]\n" + "\n".join(
        f"{name} = {toml_value(dependencies[name])}" for name in ("serde", "serde_json", "sha2"))
    text += "\n\n[profile.release]\n" + "\n".join(
        f"{key} = {toml_value(value)}" for key, value in profile.items()) + "\n"
    (crate / "Cargo.toml").write_text(text, encoding="utf-8")
    # Cargo adjusts only the harness package entry and unused dependencies;
    # shared versions remain pinned by the delivered project's lockfile.
    (crate / "Cargo.lock").write_bytes(current["lock"])
    for name, source in (("baseline", baseline), ("optimized", current)):
        (crate / f"src/modules/{name}.rs").write_bytes(source["source"])
        (crate / f"src/bin/{name}.rs").write_text(HARNESS.format(variant=name), encoding="utf-8")
    args = [cargo, "build", "--release", "--manifest-path", crate / "Cargo.toml"]
    if offline:
        args.append("--offline")
    built = command(args, timeout=600)
    (work / "build.log").write_text(built.stdout + built.stderr, encoding="utf-8")
    before = {p["name"]: p["version"] for p in tomllib.loads(current["lock"].decode())["package"]}
    after = {p["name"]: p["version"] for p in tomllib.loads((crate / "Cargo.lock").read_text())["package"]}
    for name in ("serde", "serde_json", "sha2"):
        if before[name] != after[name]:
            raise RuntimeError(f"La dépendance {name} a changé pendant la préparation du benchmark")
    return crate, profile, {name: after[name] for name in ("serde", "serde_json", "sha2")}


def fixture(path, cases):
    """Write one synthetic record at a time; these are not generated CAD meshes."""
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for i in range(cases):
            partition = "validation" if i % 5 == 1 else "test" if i % 5 == 2 else "train"
            x = i % 2
            actions = []
            for action in range(2):
                features = [float(x), float(action)] + [((i * 7 + j * 13) % 97) / 96 for j in range(126)]
                actions.append({"strategy_id": "a" + str(action), "features": features,
                                "target": .9 if action == x else .1, "accepted": True, "success": True})
            value = {"index": i, "case_id": str(i), "family": partition,
                     "geometry_id": str(i), "split": partition, "actions": actions,
                     "feature_schema": "atelier.geometry_action_features.v2",
                     "project": {"geometry": {"kind": "polygon",
                                 "outer": [[j * .1, (j % 3) * .2] for j in range(24)]}},
                     "geometry_descriptor": {"nodes": [
                         {"dim": j % 3, "tag": j, "bbox": [0., 1., 0., 1., 0., 1.]} for j in range(20)]},
                     "diagnostics": {"message": "synthetic benchmark record"}}
            stream.write(json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n")


def run_benchmark(args, work, cargo):
    baseline = source_files(args.baseline_root)
    current = source_files(ROOT)
    crate, profile, dependencies = build_harness(work, baseline, current, cargo, args.offline)
    data = work / "fixture.jsonl"
    fixture(data, args.cases)
    suffix = ".exe" if os.name == "nt" else ""
    reference = None
    runs = []
    for repeat in range(args.repetitions):
        order = ("baseline", "optimized") if repeat % 2 == 0 else ("optimized", "baseline")
        for variant in order:
            directory = work / f"run-{repeat}-{variant}"
            (directory / "shards").mkdir(parents=True)
            shard = directory / "shards/cases-000000.jsonl"
            try:
                os.link(data, shard)
            except OSError:
                shutil.copyfile(data, shard)
            request = {"corpus_dir": str(directory), "output_dir": str(directory / "output"),
                       "training": {"epochs": args.epochs, "batch_size": args.batch_size,
                                    "hidden_count": args.hidden_count, "max_records": 0,
                                    "max_seconds": args.max_seconds, "seed": 42,
                                    "warm_start": False, "resume": False, "checkpoint_batches": 16}}
            request_file = directory / "request.json"
            request_file.write_text(json.dumps(request), encoding="utf-8")
            started = time.perf_counter()
            result = command([crate / "target/release" / (variant + suffix), request_file],
                             timeout=args.max_seconds + 120)
            elapsed = time.perf_counter() - started
            report = json.loads(result.stdout)
            if report["epochs_completed"] != args.epochs or report.get("stopped_by_budget"):
                raise RuntimeError("Un entraînement a été interrompu ; ses temps ne sont pas comparables")
            model = json.loads((directory / "models/current.json").read_text(encoding="utf-8"))
            signature = {key: model[key] for key in
                         ("network", "dataset_fingerprint", "baseline_strategy", "deploy_enabled", "validation")}
            metrics = {key: report[key] for key in
                       ("training", "validation", "test", "epochs_completed", "batches_completed",
                        "training_examples", "validation_examples", "test_examples", "max_batch_observed")}
            value = {"signature": signature, "metrics": metrics}
            if reference is None:
                reference = value
            if value != reference:
                raise RuntimeError("Poids, métriques ou décisions différents : aucun gain de performance n'est publié")
            runs.append({"variant": variant, "repetition": repeat + 1,
                         "wall_seconds": elapsed, "phase_seconds": report.get("phase_seconds"),
                         "identical_weights_and_metrics": True})
            # The shared fixture remains available in an explicit work folder;
            # independent per-run copies are unnecessary after the measurement.
            shard.unlink()
            print(f"{variant} {repeat + 1}/{args.repetitions} : {elapsed:.3f} s", file=sys.stderr, flush=True)
    baseline_time = statistics.median(r["wall_seconds"] for r in runs if r["variant"] == "baseline")
    optimized_time = statistics.median(r["wall_seconds"] for r in runs if r["variant"] == "optimized")
    rustc = shutil.which("rustc") or str(Path(cargo).with_name("rustc" + suffix))
    return {"schema": "atelier.training_benchmark.v1", "generator": GENERATOR,
            "scope": "end-to-end Rust JSONL training; synthetic records only; no CAD or meshing",
            "quality_improvement_claimed": False, "cases": args.cases, "actions_per_case": 2,
            "features": FEATURES, "hidden_count": args.hidden_count, "epochs": args.epochs,
            "batch_size": args.batch_size, "repetitions_per_variant": args.repetitions,
            "fixture_bytes": data.stat().st_size, "fixture_sha256": digest(data),
            "source_sha256": {"baseline": hashlib.sha256(baseline["source"]).hexdigest(),
                              "optimized": hashlib.sha256(current["source"]).hexdigest()},
            "source_versions": {"baseline": tomllib.loads(baseline["manifest"].decode())["package"]["version"],
                                "optimized": tomllib.loads(current["manifest"].decode())["package"]["version"]},
            "release_profile": profile, "dependency_versions": dependencies,
            "cargo_lock_sha256": digest(crate / "Cargo.lock"),
            "cargo": command([cargo, "--version"]).stdout.strip(),
            "rustc": command([rustc, "--version", "--verbose"]).stdout.strip(),
            "system": platform.platform(), "runs": runs, "identical_weights_and_metrics": True,
            "result_signature_sha256": hashlib.sha256(
                json.dumps(reference, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest(),
            "baseline_median_seconds": baseline_time, "optimized_median_seconds": optimized_time,
            "speedup": baseline_time / optimized_time,
            "time_reduction_fraction": 1. - optimized_time / baseline_time,
            "work_preserved": args.work_dir is not None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline-root", type=Path, required=True,
                        help="projet/crate de référence ou archive ZIP contenant rust_core")
    parser.add_argument("--output", type=Path, required=True, help="rapport JSON à écrire")
    parser.add_argument("--cases", type=positive, default=33010)
    parser.add_argument("--repetitions", type=positive, default=3)
    parser.add_argument("--epochs", type=positive, default=3)
    parser.add_argument("--batch-size", type=positive, default=128)
    parser.add_argument("--hidden-count", type=positive, default=32)
    parser.add_argument("--max-seconds", type=positive, default=3600, help="budget par entraînement")
    parser.add_argument("--cargo", help="exécutable Cargo ; sinon recherche dans PATH")
    parser.add_argument("--offline", action="store_true", help="utiliser uniquement les dépendances Cargo déjà présentes")
    parser.add_argument("--work-dir", type=Path,
                        help="conserver les données dans un sous-dossier unique de ce répertoire")
    args = parser.parse_args(argv)
    if args.cases > 10_000_000 or args.epochs > 200 or args.batch_size > 4096 \
            or args.hidden_count > 128 or args.max_seconds > 86400:
        parser.error("Limites : 10000000 cas, 200 epochs, batch4096, couche128, budget86400s")
    cargo = args.cargo or shutil.which("cargo")
    if cargo is None:
        parser.error("Cargo est nécessaire ; installer Rust ou préciser --cargo")
    try:
        if args.work_dir is None:
            with tempfile.TemporaryDirectory(prefix="atelier-training-benchmark-") as folder:
                report = run_benchmark(args, Path(folder), cargo)
        else:
            parent = args.work_dir.expanduser().resolve()
            parent.mkdir(parents=True, exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix="training-benchmark-", dir=parent))
            (folder / ".gitignore").write_text("*\n", encoding="utf-8")
            (folder / ".ignore").write_text("*\n", encoding="utf-8")
            report = run_benchmark(args, folder, cargo)
            report["work_directory"] = str(folder)
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.name + f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyError, zipfile.BadZipFile) as error:
        print(f"Benchmark impossible : {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
