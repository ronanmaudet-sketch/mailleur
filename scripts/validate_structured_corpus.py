"""Compute a real small direct-cell pilot, separately from the million-case plan."""
from datetime import datetime
from hashlib import sha256
import platform
import subprocess
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def validate():
    from atelier.native import NativeSession, core_path
    started = time.monotonic()
    proof = ROOT / "verification/direct_quad_hex/corpus"
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    workspace, corpus = proof / "sessions" / stamp, proof / "runs" / stamp
    session = NativeSession(workspace, timeout=180)
    target = session.corpus_plan(proof / "target_plan", profile="direct_quad_hex")
    if target["planned_total"] != 1_000_000 or target["completed_cases"] != 0:
        raise RuntimeError("Le million structuré doit rester un plan de métadonnées")
    session.corpus_plan(corpus, structured=16, random=16, strategies=3, shard_size=8,
                        profile="direct_quad_hex")
    first = session.corpus_run(corpus, limit=16, max_seconds=90)
    reopened = NativeSession(workspace, timeout=180)
    status = reopened.corpus_run(corpus, limit=16, max_seconds=90)
    if first["completed_cases"] != 16 or status["completed_cases"] != 32 or not status["done"]:
        raise RuntimeError("Le pilote direct n'a pas repris ses 32 vrais cas")
    records = [json.loads(line) for shard in sorted((corpus / "shards").glob("cases-*.jsonl"))
               for line in shard.read_text(encoding="utf-8").splitlines() if line]
    if [row["index"] for row in records] != list(range(32)):
        raise RuntimeError("Indices du pilote direct invalides")
    families, shapes, splits = {}, {}, {}
    actions = 0
    for row in records:
        if families.setdefault(row["family"], row["split"]) != row["split"]:
            raise RuntimeError("Une famille directe apparaît dans plusieurs partitions")
        splits[row["split"]] = splits.get(row["split"], 0) + 1
        shape = row["project"]["mesh"]["element_shape"]
        shapes[shape] = shapes.get(shape, 0) + 1
        if row["project"]["mesh"]["order"] != 2 or row["feature_schema"] != "atelier.geometry_action_features.v3":
            raise RuntimeError("Le pilote direct doit utiliser P2 et les caractéristiques v3")
        actions += len(row["actions"])
    if len(families) != 6 or actions != 96 or set(shapes) != {"quadrilateral", "hexahedron"}:
        raise RuntimeError("Couverture du pilote direct inattendue")
    if list(corpus.rglob("mesh.msh")):
        raise RuntimeError("Le pilote direct sans conservation laisse des maillages lourds")
    training = reopened.corpus_train(corpus, epochs=3, batch_size=16, max_records=0, max_seconds=90)
    for split, key in (("train", "training_cases"), ("validation", "validation_cases"), ("test", "test_cases")):
        if training[key] != splits[split]: raise RuntimeError("Partitions directes incohérentes")
    def relative(path):
        return Path(path).resolve().relative_to(ROOT).as_posix()
    for key in ("checkpoint_path", "model_path"):
        training[key] = relative(training[key])
    core = core_path()
    engine = {
        "id": subprocess.check_output([str(core), "--engine-id"], text=True).strip(),
        "version": subprocess.check_output([str(core), "--version"], text=True).strip(),
        "sha256": sha256(core.read_bytes()).hexdigest(),
        "system": platform.system(), "architecture": platform.machine(),
    }
    summary = {"schema": "atelier.quad_hex.corpus_validation.v1", "executed": True, "engine": engine,
        "profile": "direct_quad_hex", "target_plan": {key: target[key] for key in
            ("planned_total", "structured", "random", "completed_cases", "backend_evaluations")},
        "pilot": {key: status[key] for key in ("completed_cases", "meshed_cases", "admissible_cases",
            "accepted_actions", "failed_actions", "backend_evaluations", "origin_counts", "split_counts")},
        "families": families, "shape_case_counts": shapes, "action_observations": actions,
        "second_session_resume": True, "feature_schema": "atelier.geometry_action_features.v3",
        "training": training, "directory": relative(corpus),
        "seconds": time.monotonic() - started, "million_cases_executed": False,
        "industrial_generalization_gain_claimed": False,
        "notes": ["32 configurations paramétriques réellement évaluées ; ces variantes simples ne prouvent pas une généralisation industrielle.",
                  "Les réseaux restent soumis à leurs critères d'activation mesurés."]}
    proof.mkdir(parents=True, exist_ok=True)
    (proof / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Relative links retain the association between the measured run and its weights.
    run = corpus.relative_to(proof).as_posix()
    model = (corpus / "models/current.json").stat().st_size
    model_size = f"{model:,}".replace(",", " ")
    active = "activé" if training["deploy_enabled"] else "désactivé"
    proof.joinpath("README.md").write_text(
        "# Pilote d’apprentissage sur quadrilatères et hexaèdres directs\n\n"
        f"Le moteur {engine['version']} a exécuté {len(records)} configurations sur "
        f"{engine['system']} {engine['architecture']}. Le lot comprend "
        f"{shapes['quadrilateral']} maillages quadrilatéraux et {shapes['hexahedron']} "
        f"maillages hexaédriques. Le noyau a accepté {status['accepted_actions']} des "
        f"{actions} stratégies essayées.\n\n"
        f"Le réseau a été entraîné sur {training['training_cases']} configurations. "
        f"{training['validation_cases']} autres ont servi à décider de son activation, "
        f"et {training['test_cases']} à l’évaluation finale. Le classement est {active} "
        "selon la règle de ce pilote. Sa portée est limitée aux familles géométriques testées.\n\n"
        f"- [Rapport, empreinte du moteur et résultats par partition](summary.json)\n"
        f"- [Six familles de géométrie et répartition]({run}/manifest.json)\n"
        f"- [Géométries, réglages et résultats de chaque essai]({run}/shards/)\n"
        f"- [Modèle entraîné, {model_size} octets]({run}/models/current.json)\n"
        f"- [Point de reprise de l’entraînement]({run}/models/training.checkpoint.json)\n"
        "- [Plan d’un million de configurations, avec zéro cas calculé](target_plan/manifest.json)\n",
        encoding="utf-8",
    )
    return summary


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
