"""Produire les preuves de validation sans supposer une compatibilité solveur.

Depuis le dossier racine : python scripts/validate_native_release.py
Le noyau Rust doit avoir été compilé et les dépendances installées.
"""
from __future__ import annotations

from datetime import datetime
import copy
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _relative(path):
    return str(Path(path).resolve().relative_to(ROOT))


def _quality_summary(report):
    quality = report["quality"]
    keys = ("accepted", "minimum", "mean", "poor_count", "poor_fraction", "poor_severity",
            "critical_bad_count", "critical_bad_top_count", "critical_bad_boundary_count")
    result = {key: quality.get(key) for key in keys}
    zone_keys = ("name", "kind", "dimension", "tags", "elements", "minimum", "below_minimum",
                 "boundary_elements", "boundary_minimum", "boundary_below_minimum", "accepted",
                 "buffer", "selection_method")
    result["critical_zones"] = [{key: zone.get(key) for key in zone_keys}
                                for zone in quality.get("critical_zones", [])]
    return result


def _model_summary(model):
    keys = ("schema", "model_version", "total_examples", "training_examples", "validation_examples",
            "geometry_context_groups", "training_groups", "validation_groups", "warm_started",
            "epochs", "requested_epochs", "input_count", "hidden_count", "parameter_count",
            "feature_schema", "deploy_enabled", "deployment_reason", "validation_mse",
            "constant_validation_mse", "validation_improvement", "validation_note")
    return {key: model.get(key) for key in keys} if isinstance(model, dict) else None


def validate_continuous_learning():
    """Real meshes and persistent replay; no claim of independent ML gain."""
    from atelier.native import NativeSession
    from atelier.projects import default_examples

    started = time.monotonic()
    proof_root = ROOT / "verification" / "continuous_learning"
    proof_root.mkdir(parents=True, exist_ok=True)
    # Each validation invocation starts with an empty, isolated history. Raw
    # older reports are retained under their own session directory.
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    workspace = proof_root / "continuous_runs" / stamp
    session = NativeSession(workspace, timeout=120)
    cases = {case["id"]: case for case in default_examples()}
    selected = [(identifier, copy.deepcopy(cases[identifier]["project"]), None)
                for identifier in ("plate", "chamfer", "bracket", "shell", "polygon")]
    imported = json.loads((ROOT / "examples" / "atelier_import.json").read_text(encoding="utf-8"))
    imported["name"] = "Apprentissage de validation — STEP deux volumes"
    imported["mesh"].update(size=.8, min_size=.15, max_elements=20000)
    source = ROOT / "examples" / "assemblage_deux_volumes.step"
    if not source.is_file():
        raise RuntimeError("La source STEP deux volumes manque ; cet essai ne peut pas être déclaré réussi")
    selected.append(("import_step_two_volumes", imported, source))
    results = []
    first_project = first_result = None
    for identifier, project, source in selected:
        project["units"] = {"input": "mm", "internal": "m"}
        project["quality_policy"] = {"minimum_quality": .05, "poor_threshold": .2, "strict": True}
        project.pop("learning", None)
        inspection = session.inspect(project, source=source)
        catalog = inspection["report"]["entity_catalog"]
        # Use the actual catalogue: boolean operations can renumber faces.
        # On the sphere the complete surface is protected, avoiding a
        # degenerate polar edge as the only selected region.
        dimension = 2 if project["mode"] in ("surface", "volume") else 1
        candidates = [item for item in catalog if item.get("dimension", item.get("dim")) == dimension]
        if not candidates:
            raise RuntimeError(f"Aucune entité dimension {dimension} pour protéger {identifier}")
        tag = min(item["tag"] for item in candidates)
        project["critical_zones"] = [{"name": f"Zone protégée {identifier}",
                                      "kind": "interface" if project["mode"] == "volume" else "contact",
                                      "dimension": dimension, "tags": [tag], "buffer": .05,
                                      "minimum_quality": .2}]
        case_started = time.monotonic()
        result = session.learn(project, source=source, max_trials=4, epochs=30, seed=42, max_seconds=60)
        report, experience = result["report"], result["report"]["experience"]
        if report["quality"].get("accepted") is not True or experience.get("accepted") is not True:
            raise RuntimeError(f"La recherche {identifier} n'a pas produit de résultat accepté")
        if not 1 <= experience["executed_trials"] <= 4:
            raise RuntimeError(f"Budget d'essais non respecté pour {identifier}")
        if not report["quality"].get("critical_zones") or not all(
                zone.get("accepted") is True and zone.get("elements", 0) > 0
                for zone in report["quality"]["critical_zones"]):
            raise RuntimeError(f"Zone critique non évaluée ou refusée pour {identifier}")
        if identifier == "import_step_two_volumes" and report["geometry_diagnostics"]["entity_counts"].get("3") != 2:
            raise RuntimeError("Le test importé ne contient pas les deux volumes attendus")
        record = {"id": identifier, "context_id": experience["context_id"],
                  "geometry_id": experience["geometry_id"], "source_imported": source is not None,
                  "accepted": experience["accepted"], "executed_trials": experience["executed_trials"],
                  "max_trials": 4, "memory_reused": experience["memory_reused"],
                  "nodes": report["nodes"], "elements": report["elements"],
                  "quality": _quality_summary(report), "model": _model_summary(experience.get("learning")),
                  "directory": _relative(result["directory"]),
                  "seconds": time.monotonic() - case_started}
        results.append(record)
        if first_result is None:
            first_project, first_result = copy.deepcopy(project), result
    geometry_ids = {item["geometry_id"] for item in results}
    if len(geometry_ids) != 6:
        raise RuntimeError("La validation continue doit contenir six géométries distinctes")

    # Simulate the GUI reopening the winning settings in another Python
    # session. The shared disk history, not an in-process Python cache,
    # must provide the old recipe, which is generated and checked again.
    reopened = NativeSession(workspace, timeout=120)
    chosen = copy.deepcopy(first_result["report"]["experience"]["chosen_project"])
    replay = reopened.learn(chosen, max_trials=1, epochs=30, seed=42, max_seconds=60)
    replay_experience = replay["report"]["experience"]
    if not replay_experience["memory_reused"] or replay_experience["executed_trials"] != 1:
        raise RuntimeError("La recette gagnante n'a pas été retrouvée et remaillée en un essai")
    if replay["report"]["quality"].get("accepted") is not True:
        raise RuntimeError("La recette mémorisée n'a pas été revalidée")
    replay_summary = {"memory_reused": True, "executed_trials": replay_experience["executed_trials"],
                      "context_id": replay_experience["context_id"],
                      "geometry_id": replay_experience["geometry_id"],
                      "quality": _quality_summary(replay["report"]), "new_python_session": True,
                      "directory": _relative(replay["directory"])}

    changed = copy.deepcopy(first_project)
    changed["critical_zones"][0]["buffer"] = .15
    change = reopened.learn(changed, max_trials=2, epochs=30, seed=42, max_seconds=60)
    changed_experience = change["report"]["experience"]
    if changed_experience["context_id"] == first_result["report"]["experience"]["context_id"]:
        raise RuntimeError("Une modification de la zone protégée n'a pas changé le contexte")
    if changed_experience["geometry_id"] != first_result["report"]["experience"]["geometry_id"]:
        raise RuntimeError("Une modification de priorité seule a changé l'identité de la géométrie")
    if changed_experience["memory_reused"]:
        raise RuntimeError("Le contexte de zone modifié a réutilisé une ancienne acceptation")
    changed_summary = {"context_changed": True, "geometry_unchanged": True,
                       "memory_reused": changed_experience["memory_reused"],
                       "context_id": changed_experience["context_id"],
                       "quality": _quality_summary(change["report"]),
                       "directory": _relative(change["directory"])}

    impossible = copy.deepcopy(first_project)
    impossible["critical_zones"][0]["minimum_quality"] = 1.0
    try:
        reopened.learn(impossible, max_trials=2, epochs=30, seed=42, max_seconds=60)
    except RuntimeError as error:
        failed_directory = Path(reopened.last_attempt_directory)
        failed_report = json.loads((failed_directory / "report.json").read_text(encoding="utf-8"))
        if failed_report.get("accepted") is not False or failed_report["experience"].get("accepted") is not False:
            raise RuntimeError("Le refus attendu n'est pas prouvé par le rapport") from error
        if (failed_directory / "mesh.msh").exists():
            raise RuntimeError("Un maillage public a été publié malgré l'échec qualité") from error
        failed_summary = {"expected_rejection": True, "public_mesh_absent": True,
                          "executed_trials": failed_report["experience"]["executed_trials"],
                          "context_id": failed_report["experience"]["context_id"],
                          "directory": _relative(failed_directory), "error": str(error)}
    else:
        raise RuntimeError("Le seuil volontairement impossible a été accepté")

    memory = reopened.memory_info()
    if memory["geometry_count"] < 6 or memory["failed_observations"] < 1:
        raise RuntimeError("La mémoire ne contient pas les géométries et le refus attendus")
    if memory["journal_events"] < sum(item["executed_trials"] for item in results) + 1:
        raise RuntimeError("Le journal ne couvre pas les essais réellement exécutés")
    memory["model"] = _model_summary(memory.get("model"))
    memory["memory_path"] = _relative(memory["memory_path"])
    summary = {"executed": True, "distinct_geometries": len(geometry_ids), "cases": results,
               "exact_recipe_replay": replay_summary, "changed_critical_zone": changed_summary,
               "impossible_quality_rejection": failed_summary, "memory": memory,
               "workspace": _relative(workspace), "seconds": time.monotonic() - started,
               "independent_generalization_gain_claimed": False,
               "notes": ["Vrais maillages et évaluations natifs sur six géométries.",
                         "Les versions et métriques du modèle sont rapportées sans transformer leur validation en preuve industrielle indépendante.",
                         "Aucun solveur mécanique ni calcul automatique des contraintes n'est exécuté."]}
    (proof_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def validate_corpus():
    """Plan one million metadata-only cases, then compute a separate 48-case pilot.

    No corpus size is inferred from planning. The pilot records real native
    meshing results; synthetic JSONL fixtures in the unit suite remain a
    separately identified test of loading and training capacity.
    """
    from atelier.native import NativeSession

    started = time.monotonic()
    proof_root = ROOT / "verification" / "corpus"
    proof_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    workspace = proof_root / "sessions" / stamp
    session = NativeSession(workspace, timeout=180)
    target_directory = proof_root / "target_plan"
    target = session.corpus_plan(corpus_dir=target_directory)
    if (target["structured"], target["random"], target["planned_total"]) != (250000, 750000, 1000000):
        raise RuntimeError("Le plan cible ne correspond pas aux deux quotas demandés")
    if target["completed_cases"] != 0 or target["backend_evaluations"] != 0:
        raise RuntimeError("La planification cible ne doit pas être présentée comme un million de cas calculés")
    target_files = [file for file in target_directory.rglob("*") if file.is_file()]
    if len(target_files) > 10 or sum(file.stat().st_size for file in target_files) > 100000:
        raise RuntimeError("La planification d'un million de cas a produit plus que ses seules métadonnées")

    pilot_directory = proof_root / "pilot" / stamp
    plan = session.corpus_plan(corpus_dir=pilot_directory, structured=24, random=24,
                               seed=42, strategies=2, shard_size=12)
    first = session.corpus_run(corpus_dir=pilot_directory, limit=24, max_seconds=90,
                               retain_meshes=False)
    if first["processed_cases"] != 24 or first["completed_cases"] != 24:
        raise RuntimeError("Le premier lot pilote n'a pas terminé ses 24 cas")
    reopened = NativeSession(workspace, timeout=180)
    second = reopened.corpus_run(corpus_dir=pilot_directory, limit=24, max_seconds=90,
                                 retain_meshes=False)
    if second["processed_cases"] != 24 or second["completed_cases"] != 48 or not second["done"]:
        raise RuntimeError("La nouvelle session n'a pas repris les 24 cas restants")

    indices = []
    families = {}
    origins = {}
    splits = {}
    failure_kinds = {}
    inspections = 0
    inspection_successes = 0
    actions_total = 0
    accepted_actions = 0
    feature_schemas = set()
    feature_counts = set()
    backend_evaluations = 0
    for shard in sorted((pilot_directory / "shards").glob("cases-*.jsonl")):
        with shard.open(encoding="utf-8") as stream:
            for line in stream:
                record = json.loads(line)
                indices.append(record["index"])
                family, split = record["family"], record["split"]
                if families.setdefault(family, split) != split:
                    raise RuntimeError("Une famille du pilote apparaît dans plusieurs partitions")
                origins[record["origin"]] = origins.get(record["origin"], 0) + 1
                splits[split] = splits.get(split, 0) + 1
                feature_schemas.add(record["feature_schema"])
                feature_counts.add(record["feature_count"])
                inspections += 1
                inspection_successes += int(record["reference_inspection_success"])
                backend_evaluations += record["backend_evaluations"]
                for action in record["actions"]:
                    actions_total += 1
                    accepted_actions += int(action["accepted"])
                    if len(action["features"]) != 128:
                        raise RuntimeError("Un exemple du corpus n'utilise pas les 128 caractéristiques attendues")
                    if not action["success"]:
                        kind = action.get("failure_kind") or "unclassified"
                        failure_kinds[kind] = failure_kinds.get(kind, 0) + 1
    if indices != list(range(48)) or len(families) != 24:
        raise RuntimeError("Le pilote comporte un doublon, un trou ou un nombre inattendu de familles")
    if origins != {"structured": 24, "random": 24} or set(splits) != {"train", "validation", "test"}:
        raise RuntimeError("Les origines ou partitions du pilote ne sont pas celles prévues")
    if feature_counts != {128} or len(feature_schemas) != 1:
        raise RuntimeError("Les caractéristiques du pilote ne partagent pas un schéma compatible")
    if actions_total != 96 or not accepted_actions or not failure_kinds.get("invalid_geometry"):
        raise RuntimeError("Le pilote doit contenir ses stratégies, des résultats acceptés et les défauts invalides attendus")
    if backend_evaluations != second["backend_evaluations"]:
        raise RuntimeError("Le compteur d'évaluations ne correspond pas aux cas réellement journalisés")
    if list(pilot_directory.rglob("mesh.msh")):
        raise RuntimeError("Le pilote sans conservation des maillages laisse des fichiers de calcul inattendus")

    training = reopened.corpus_train(corpus_dir=pilot_directory, epochs=2, batch_size=16,
                                     max_records=0, max_seconds=90)
    if training.get("streaming") is not True or training["input_count"] != 128:
        raise RuntimeError("L'entraînement pilote n'utilise pas le parcours natif par mini-lots à 128 entrées")
    if training["epochs_completed"] != 2 or training["max_batch_observed"] > 16:
        raise RuntimeError("Le budget de mini-lot ou le nombre de passages pilote n'est pas celui exécuté")
    for split, key in (("train", "training_cases"), ("validation", "validation_cases"), ("test", "test_cases")):
        if training[key] != splits[split]:
            raise RuntimeError("Les partitions d'entraînement ne correspondent pas aux cas enregistrés")
    if training["test_used_for_gradients"] or training["test_used_for_weight_selection"]:
        raise RuntimeError("Le jeu de test a été utilisé pour entraîner ou sélectionner les poids")
    installed = workspace / "models" / "corpus.json"
    model_path = Path(training["model_path"])
    if not installed.is_file() or installed.read_bytes() != model_path.read_bytes():
        raise RuntimeError("Le conseiller de corpus n'a pas été installé fidèlement dans l'atelier")
    model = json.loads(model_path.read_text(encoding="utf-8"))
    status = reopened.corpus_status(corpus_dir=pilot_directory)

    summary_keys = ("planned_total", "structured", "random", "completed_cases", "generated_cases",
                    "meshed_cases", "admissible_cases", "accepted_actions", "failed_actions",
                    "backend_evaluations", "split_counts", "origin_counts", "family_count", "done")
    target_summary = {key: target.get(key) for key in summary_keys}
    target_summary.update(directory=_relative(target_directory), metadata_files=len(target_files),
                          metadata_bytes=sum(file.stat().st_size for file in target_files),
                          million_cases_executed=False)
    pilot_summary = {key: status.get(key) for key in summary_keys}
    pilot_summary.update(directory=_relative(pilot_directory), records=len(indices), actions=actions_total,
                         reference_inspections=inspections, successful_reference_inspections=inspection_successes,
                         failure_kinds=failure_kinds, feature_count=128, feature_schema=next(iter(feature_schemas)),
                         family_splits=families, raw_meshes_retained=False,
                         first_batch_completed=first["completed_cases"], second_batch_completed=second["completed_cases"],
                         resumed_in_new_python_session=True)
    training_keys = ("schema", "computation_backend", "streaming", "input_count", "hidden_count",
                     "parameter_count", "feature_schema", "training_cases", "training_examples",
                     "validation_cases", "validation_examples", "test_cases", "test_examples",
                     "epochs_requested", "epochs_completed", "batch_size", "max_batch_observed",
                     "batches_completed", "warm_started", "resumed", "stopped_by_budget", "phase",
                     "checkpoint_complete", "baseline_strategy", "training", "validation", "test",
                     "validation_selection_improvement", "deploy_enabled", "model_version",
                     "test_used_for_gradients", "test_used_for_weight_selection", "phase_seconds", "seconds")
    training_summary = {key: training.get(key) for key in training_keys}
    training_summary.update(model_path=_relative(model_path), checkpoint_path=_relative(training["checkpoint_path"]),
                            installed_model_path=_relative(installed), model_schema=model["schema"],
                            model_dataset_fingerprint=model.get("dataset_fingerprint"),
                            model_activation_reason=model.get("deployment_reason"))
    summary = {"executed": True, "target_plan": target_summary, "pilot": pilot_summary,
               "training": training_summary, "workspace": _relative(workspace),
               "seconds": time.monotonic() - started, "million_cases_executed": False,
               "industrial_generalization_gain_claimed": False,
               "notes": ["Le million est planifié sous forme de métadonnées ; seuls les 48 cas du pilote sont calculés ici.",
                         "Les évaluations natives comprennent les inspections de référence et les stratégies réellement exécutées.",
                         "Les cas de géométrie invalide restent des échecs étiquetés, pas des maillages acceptés.",
                         "La suite comporte aussi des enregistrements JSONL synthétiques pour tester le streaming ; ce ne sont pas des maillages supplémentaires.",
                         "L'activation du conseiller est rapportée telle que mesurée, sans promesse de gain industriel."]}
    (proof_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main():
    from atelier import __version__
    from atelier.native import NativeSession, core_path
    from atelier.projects import default_examples
    directory = ROOT / "verification" / "native_release"
    directory.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                           cwd=ROOT, env=environment, capture_output=True, text=True)
    test_text = tests.stdout + tests.stderr
    (directory / "python_tests.txt").write_text(test_text, encoding="utf-8")
    match = re.search(r"Ran (\d+) tests in ([0-9.]+)s", test_text)
    if tests.returncode:
        raise RuntimeError("La suite Python a échoué ; consulter verification/native_release/python_tests.txt")
    cargo = shutil.which("cargo")
    rust_report = {"executed": False, "reason": "Cargo absent ; utiliser les preuves fournies ou installer Rust pour les reproduire"}
    if cargo:
        rust = subprocess.run([cargo, "test", "--locked", "--manifest-path", str(ROOT / "rust_core/Cargo.toml")],
                              cwd=ROOT, env=environment, capture_output=True, text=True)
        text = rust.stdout + rust.stderr
        (directory / "rust_tests.txt").write_text(text, encoding="utf-8")
        counts = re.findall(r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored", text)
        rust_report = {"executed": True, "exit_code": rust.returncode,
                       "passed": sum(int(item[0]) for item in counts) if counts else None,
                       "failed": sum(int(item[1]) for item in counts) if counts else None,
                       "ignored": sum(int(item[2]) for item in counts) if counts else None}
        if rust.returncode: raise RuntimeError("Les tests Rust ont échoué")
    session = NativeSession(directory / "runs")
    examples = []
    for example in default_examples():
        project = example["project"]
        project["units"] = {"input": "mm", "internal": "m"}
        (ROOT / "examples" / ("atelier_" + example["id"] + ".json")).write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = session.generate(project)
        export = session.export(Path(result["directory"]), directory / "examples" / example["id"], "bundle", units="mm", planar=project["mode"] == "planar")
        examples.append({"id": example["id"], "name": example["name"], "nodes": result["report"]["nodes"],
                         "elements": result["report"]["elements"], "quality": result["report"]["quality"],
                         "native_kernel": result["report"]["native_kernel"],
                         "export_complete": export.get("complete"),
                         "export_statuses": [{"format": v["format"], "status": v["status"]} for v in export.get("results", [])],
                         "directory": str(Path(result["directory"]).relative_to(ROOT))})
    training = session.train(project=default_examples()[0]["project"], epochs=200)
    learning = {key: training["report"].get(key) for key in ("training_cases", "validation_cases", "validation_guide_reward", "validation_fixed_reward", "improvement", "deploy_enabled", "native_kernel")}
    learning["directory"] = str(Path(training["directory"]).relative_to(ROOT))
    continuous_learning = validate_continuous_learning()
    corpus = validate_corpus()
    try: timestamp = datetime.now(ZoneInfo("Europe/Paris")).isoformat()
    except ZoneInfoNotFoundError: timestamp = datetime.now().astimezone().isoformat()
    report = {"version": __version__, "date": timestamp,
              "system": platform.platform(), "python": platform.python_version(), "core_binary": str(core_path()),
              "python_tests": {"exit_code": tests.returncode, "total": int(match[1]) if match else None,
                               "skipped": len(re.findall(r"\.\.\. skipped ", test_text)),
                               "skipped_details": [line for line in test_text.splitlines() if "... skipped " in line]},
              "rust_tests": rust_report, "examples": examples, "learning": learning,
              "continuous_learning": continuous_learning,
              "corpus": corpus,
              "test_scope": {"retired_python_prototype_tests": 57,
                             "replacement_boundary_and_io_tests": 16,
                             "reference": "tests/retired_reference/README.txt",
                             "retirement_counted_as_pass_or_skip": False,
                             "synthetic_streaming_fixture_is_real_meshing": False,
                             "note": "Les API numériques Python ont été retirées explicitement ; les totaux rc3 et rc4 ne portent pas sur exactement le même périmètre."},
              "macos_windows_executed": False,
              "external_solvers_executed": False,
              "limitations": ["Tests effectués sous Linux, pas sur macOS ni Windows.",
                              "Relecture de formats testée ; aucun solveur externe exécuté.",
                              "Reconstruction indépendante, pas restauration du noyau Rust original.",
                              "Remaillage local par entités, sans édition libre d'un sous-ensemble de cellules."]}
    (directory / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"python_tests": report["python_tests"], "rust_tests": rust_report,
                      "learning": learning,
                      "continuous_learning": {key: continuous_learning[key] for key in ("distinct_geometries", "seconds", "exact_recipe_replay", "memory")},
                      "corpus": {"million_cases_executed": False, "target_plan": corpus["target_plan"],
                                 "pilot": corpus["pilot"], "training": corpus["training"]},
                      "examples": [{k: v for k, v in item.items() if k in ("id", "nodes", "elements", "export_complete")} for item in examples]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
