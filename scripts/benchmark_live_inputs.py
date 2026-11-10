"""Comparer les calculs du guide interactif, hors CAO et maillage.

Exemple : python scripts/benchmark_live_inputs.py --baseline archive_rc5.zip
Deux copies du module Rust sont compilées avec les mêmes dépendances et le
même profil. Les mesures incluent les passages avant et les gradients, puis
comparent les poids et résultats exacts. Fixtures synthétiques uniquement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
pub fn benchmark(rows: usize, epochs: usize, repetitions: usize) -> Value {
    use std::hint::black_box;
    use std::time::Instant;
    let mut results = Vec::new();
    for saturated in [false, true] {
        let examples: Vec<_> = (0..rows).map(|row| Example {
            features: std::array::from_fn(|column| {
                let x = ((row * 17 + column * 31) % 997) as f64 / 996.;
                if saturated { x * 16. - 8. } else { x }
            }),
            target: (row % 11) as f64 / 10.,
            group: "fixture".into(),
        }).collect();
        let mut forward_seconds = Vec::new();
        let mut training_seconds = Vec::new();
        let mut final_network = Value::Null;
        let mut final_network_bits = Vec::new();
        let mut final_predictions = Vec::new();
        for _ in 0..repetitions {
            let mut network = Network::new(42, 0.5);
            // Warm the code/data without changing the measured initial weights.
            for row in examples.iter().take(64) {
                black_box(network.forward(black_box(&row.features)));
            }
            let started = Instant::now();
            for _ in 0..epochs {
                for row in &examples {
                    black_box(network.forward(black_box(&row.features)));
                }
            }
            forward_seconds.push(started.elapsed().as_secs_f64());
            let started = Instant::now();
            for _ in 0..epochs {
                for row in &examples {
                    network.step(black_box(row), black_box(0.05));
                }
            }
            training_seconds.push(started.elapsed().as_secs_f64());
            final_predictions = examples.iter().take(64)
                .map(|row| network.forward(&row.features).1.to_bits()).collect();
            let value = network.value();
            if !final_network.is_null() { assert_eq!(final_network, value); }
            final_network = value;
            final_network_bits = network.input.iter().flatten().chain(network.hidden.iter())
                .chain(network.output.iter()).chain(std::iter::once(&network.bias))
                .map(|value| value.to_bits()).collect::<Vec<_>>();
        }
        results.push(json!({"saturated":saturated,"rows":rows,"epochs":epochs,
            "forward_seconds":forward_seconds,"training_seconds":training_seconds,
            "network":final_network,"network_bits":final_network_bits,"prediction_bits":final_predictions}));
    }
    json!({"fixtures":results})
}
'''


def command(args, cwd):
    result = subprocess.run([str(x) for x in args], cwd=cwd, capture_output=True,
                            text=True, timeout=600)
    if result.returncode:
        raise RuntimeError(result.stderr[-8000:])
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--rows", type=int, default=8192)
    parser.add_argument("--epochs", type=int, default=16)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    # The build runs in a temporary crate. Preserve a caller-supplied relative
    # executable path before changing the subprocess working directory.
    if "/" in args.cargo or "\\" in args.cargo:
        args.cargo = str(Path(args.cargo).expanduser().resolve())
    if min(args.rows, args.epochs, args.repetitions) < 1:
        parser.error("Les tailles et répétitions doivent être positives")
    if args.baseline.is_file() and zipfile.is_zipfile(args.baseline):
        with zipfile.ZipFile(args.baseline) as archive:
            names = [n for n in archive.namelist()
                     if n.endswith("rust_core/src/experience_model.rs")]
            if len(names) != 1:
                parser.error("Une seule source experience_model.rs est requise")
            baseline = archive.read(names[0])
    else:
        baseline = args.baseline.read_bytes()
    current = (ROOT / "rust_core/src/experience_model.rs").read_bytes()
    with tempfile.TemporaryDirectory(prefix="atelier-live-inputs-") as folder:
        work = Path(folder)
        (work / "src/bin").mkdir(parents=True)
        (work / "src/modules").mkdir()
        (work / "Cargo.toml").write_text(
            '[package]\nname="atelier-live-inputs-benchmark"\nversion="0.1.0"\nedition="2021"\n'
            '[dependencies]\nserde_json="1"\n[profile.release]\nlto="thin"\ncodegen-units=1\n',
            encoding="utf-8")
        shutil.copyfile(ROOT / "rust_core/Cargo.lock", work / "Cargo.lock")
        for name, source in (("baseline", baseline), ("candidate", current)):
            (work / f"src/modules/{name}.rs").write_bytes(source + HARNESS.encode())
            (work / f"src/bin/{name}.rs").write_text(
                '#![recursion_limit="256"]\n'
                'mod representation { pub const INPUTS:usize=128; '
                'pub const SCHEMA:&str="atelier.geometry_action_features.v2"; }\n'
                f'#[path="../modules/{name}.rs"] mod guide;\n'
                'fn main() { let n:Vec<usize>=std::env::args().skip(1)'
                '.map(|x|x.parse().unwrap()).collect(); '
                'println!("{}",guide::benchmark(n[0],n[1],n[2])); }\n',
                encoding="utf-8")
        command([args.cargo, "build", "--release"] + (["--offline"] if args.offline else []), work)
        variants = {}
        for name in ("baseline", "candidate"):
            exe = work / "target/release" / (name + (".exe" if platform.system() == "Windows" else ""))
            variants[name] = json.loads(command(
                [exe, args.rows, args.epochs, args.repetitions], work))["fixtures"]
    comparisons = []
    for before, after in zip(variants["baseline"], variants["candidate"]):
        equal = (before["network_bits"] == after["network_bits"]
                 and before["prediction_bits"] == after["prediction_bits"])
        if not equal:
            raise RuntimeError("Les résultats numériques diffèrent : optimisation refusée")
        result = {"saturated":before["saturated"], "rows":args.rows, "epochs":args.epochs,
                  "exact_network_and_prediction_bits":equal}
        for phase in ("forward", "training"):
            values = {name: variant[f"{phase}_seconds"]
                      for name, variant in (("baseline", before), ("candidate", after))}
            medians = {name: statistics.median(times) for name, times in values.items()}
            result[phase] = {"seconds":values, "median_seconds":medians,
                             "speedup":medians["baseline"] / medians["candidate"]}
        comparisons.append(result)
    report = {"schema":"atelier.live_input_benchmark.v1", "synthetic":True,
              "scope":"MLP only; excludes CAD, meshing, replay preparation and model publication",
              "platform":platform.platform(), "machine":platform.machine(),
              "cargo":command([args.cargo, "--version"], ROOT).strip(),
              "rustc":command([Path(shutil.which(args.cargo) or args.cargo).with_name("rustc"),
                               "--version"], ROOT).strip(),
              "source_sha256":{"baseline":hashlib.sha256(baseline).hexdigest(),
                               "candidate":hashlib.sha256(current).hexdigest()},
              "repetitions":args.repetitions, "comparisons":comparisons}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
