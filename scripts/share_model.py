#!/usr/bin/env python3
"""Créer un ZIP léger d'un guide JSON, sans importer de corpus ou de mémoire.

Les contrôles Python portent sur le format et les preuves déclarées. Ils ne
remplacent pas la validation numérique du noyau Rust ni un essai sur les pièces
du destinataire. Le script n'active jamais un guide désactivé.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import zipfile


MAX_BYTES = 8 * 1024 * 1024
FEATURE_SCHEMA = "atelier.geometry_action_features.v2"
FEATURES = ["mode", "thin_extent", "middle_extent", "relative_size", "hole_fraction",
            "curved", "relative_corner_size", "boundary_complexity"]
ALGORITHMS = ["automatic", "delaunay", "frontal"]
DESTINATIONS = {"historique": "models/guide.json", "corpus": "models/corpus.json",
                "experience": "experience/model.json"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value, limit=None):
    require(type(value) in (int, float), "Un poids ou indicateur doit être numérique, sans booléen")
    require(abs(value) <= sys.float_info.max and math.isfinite(value), "Un poids ou indicateur n'est pas fini")
    if limit is not None:
        require(abs(value) <= limit, "Un poids est hors des bornes du noyau")
    return value


def integer(value, minimum, maximum=None):
    require(type(value) is int and value >= minimum and (maximum is None or value <= maximum),
            "Un entier de dimension, compte ou version est invalide")
    return value


def vector(value, length, limit):
    require(type(value) is list and len(value) == length, "Dimensions d'un vecteur de poids invalides")
    for item in value:
        number(item, limit)


def matrix(value, height, width, limit):
    require(type(value) is list and len(value) == height, "Dimensions d'une matrice de poids invalides")
    for row in value:
        vector(row, width, limit)


def names(model, key, *, identifiers=False):
    values = model.get(key)
    require(type(values) is list, "Provenance absente : " + key)
    require(all(type(v) is str and 0 < len(v.encode("utf-8")) <= 1024 for v in values),
            "Provenance invalide : " + key)
    require(len(values) == len(set(values)), "Provenance dupliquée : " + key)
    if identifiers:
        require(all(is_digest(v) for v in values), "Identité géométrique invalide : " + key)
    return set(values)


def is_digest(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def finite_tree(value):
    if type(value) is float:
        number(value)
    elif type(value) is dict:
        for child in value.values():
            finite_tree(child)
    elif type(value) is list:
        for child in value:
            finite_tree(child)


def validate_model(kind, model):
    require(kind in DESTINATIONS, "Type de guide inconnu")
    require(type(model) is dict, "Le modèle doit être un objet JSON")
    finite_tree(model)
    require(type(model.get("deploy_enabled")) is bool, "Décision d'activation absente")
    enabled = model["deploy_enabled"]
    if kind == "historique":
        require(model.get("schema") == "atelier.advisor.v1" and type(model.get("schema_version")) is int
                and model["schema_version"] == 1, "Schéma du guide historique incompatible")
        require(model.get("features") == FEATURES and model.get("algorithms") == ALGORITHMS,
                "Caractéristiques ou algorithmes du guide historique incompatibles")
        require(type(model.get("hidden_bias")) is list, "Biais cachés absents")
        width = integer(len(model["hidden_bias"]), 1, 128)
        matrix(model.get("input_weights"), width, 8, 1e6)
        vector(model.get("hidden_bias"), width, 1e6)
        matrix(model.get("output_weights"), 3, width, 1e6)
        vector(model.get("output_bias"), 3, 1e6)
        require(model.get("baseline_algorithm") in ALGORITHMS, "Algorithme de référence absent")
        require(type(model.get("baseline_successful")) is bool, "État de référence absent")
        signature = model.get("mesh_signature")
        require(type(signature) is dict and set(signature) == {"order", "threads", "min_size", "recombine", "repair_enabled"},
                "Signature de maillage incompatible")
        integer(signature["order"], 1, 2)
        integer(signature["threads"], 1, 64)
        require(number(signature["min_size"]) > 0, "Taille minimale du guide invalide")
        require(type(signature["recombine"]) is bool and type(signature["repair_enabled"]) is bool,
                "Signature de maillage invalide")
        ranges = model.get("feature_ranges")
        require(type(ranges) is list and len(ranges) == 8, "Domaine de caractéristiques absent")
        for limits in ranges:
            vector(limits, 2, 1e6)
            require(0 <= limits[0] <= limits[1] <= 1, "Domaine de caractéristiques invalide")
        train = names(model, "training_geometry_ids", identifiers=True)
        validation = names(model, "validation_geometry_ids", identifiers=True)
        require(train and validation and train.isdisjoint(validation), "Géométries entraînement/validation incompatibles")
        families = names(model, "training_families")
        valid_families = names(model, "validation_families")
        require(families.isdisjoint(valid_families), "Familles entraînement/validation incompatibles")
        modes = names(model, "validated_modes")
        kinds = names(model, "trained_kinds")
        require(modes <= {"planar", "surface", "volume"} and kinds <= {"plate", "polygon", "box", "sphere", "bracket"},
                "Domaine géométrique du guide incompatible")
        require(type(model.get("gmsh_version")) is str and model["gmsh_version"], "Version Gmsh absente")
        improvement = number(model.get("improvement"))
        guide_reward = number(model.get("validation_guide_reward"))
        baseline = number(model.get("validation_fixed_reward"))
        require(abs(improvement - (guide_reward - baseline)) <= 1e-12, "Comparaison de validation incohérente")
        require(not enabled or improvement > 1e-4, "Activation non justifiée")
        return {"inputs": 8, "hidden": width, "outputs": 3, "parameters": 12 * width + 3}

    require(model.get("feature_schema") == FEATURE_SCHEMA and type(model.get("input_count")) is int
            and model["input_count"] == 128, "Le guide demande les 128 caractéristiques de production v2")
    width = integer(model.get("hidden_count"), 1, 128)
    network = model.get("network")
    require(type(network) is dict, "Poids du réseau absents")
    if kind == "experience":
        require(model.get("schema") == "atelier.experience_model.v2" and type(model.get("schema_version")) is int
                and model["schema_version"] == 2 and width == 12, "Schéma ou architecture du guide interactif incompatible")
        require(type(model.get("output_count")) is int and model["output_count"] == 1, "Sortie du guide interactif invalide")
        train = names(model, "training_groups")
        validation = names(model, "validation_groups")
        require(train and train.isdisjoint(validation), "Groupes entraînement/validation incompatibles")
        integer(model.get("revision"), 1)
        if enabled:
            require(len(train) >= 2 and validation and number(model.get("validation_improvement")) > 1e-5,
                    "Activation du guide interactif non justifiée")
        bound = 1e3
    else:
        require(model.get("schema") == "atelier.corpus.model.v1", "Schéma du guide de corpus incompatible")
        integer(model.get("model_version"), 1)
        require(is_digest(model.get("dataset_fingerprint")), "Empreinte du corpus absente ou invalide")
        families = model.get("family_splits")
        require(type(families) is dict and families and all(type(k) is str and k and v in ("train", "validation", "test")
                for k, v in families.items()), "Partitions du corpus absentes ou invalides")
        require(type(model.get("weights_trained")) is bool, "État d'entraînement absent")
        require(type(model.get("baseline_strategy")) is str and model["baseline_strategy"], "Stratégie de référence absente")
        evidence = model.get("validation")
        require(type(evidence) is dict, "Métriques de validation absentes")
        if enabled:
            integer(evidence.get("cases"), 2)
            scores = {k: number(evidence.get(k)) for k in ("model_reward", "baseline_reward", "selected_accepted_rate", "baseline_accepted_rate")}
            require(all(0 <= v <= 1 for v in scores.values()), "Métriques de validation hors plage")
            require(model["weights_trained"] and scores["model_reward"] - scores["baseline_reward"] > 1e-5
                    and scores["selected_accepted_rate"] + 1e-12 >= scores["baseline_accepted_rate"]
                    and type(evidence.get("missing_baseline_cases")) is int and evidence["missing_baseline_cases"] == 0,
                    "Activation du guide de corpus non justifiée")
        bound = 1e4
    matrix(network.get("input_weights"), width, 128, bound)
    vector(network.get("hidden_bias"), width, bound)
    vector(network.get("output_weights"), width, bound)
    number(network.get("output_bias"), bound)
    count = 130 * width + 1
    require(type(model.get("parameter_count")) is int and model["parameter_count"] == count,
            "Nombre de paramètres incohérent")
    return {"inputs": 128, "hidden": width, "outputs": 1, "parameters": count}


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Clé JSON dupliquée : " + key)
        result[key] = value
    return result


def load_json(path):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    require(len(data) <= MAX_BYTES, "Fichier trop volumineux : maximum 8 Mio")
    def invalid_constant(value):
        raise ValueError("Constante JSON non finie : " + value)
    value = json.loads(data, parse_constant=invalid_constant, object_pairs_hook=strict_object)
    finite_tree(value)
    return data, value


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")


def share_model(kind, model_path, output, report_path=None):
    source_bytes, model = load_json(model_path)
    architecture = validate_model(kind, model)
    # Ce champ du guide historique embarque des géométries. Il n'est pas
    # nécessaire au chargeur Rust de prédiction et n'est pas diffusé ici.
    omitted = []
    if kind == "historique" and "training_configs" in model:
        model = dict(model)
        del model["training_configs"]
        omitted.append("training_configs")
    entries = {"model.json": json_bytes(model)}
    if report_path is not None:
        _, report = load_json(report_path)
        require(type(report) is dict, "Le rapport doit être un objet JSON")
        entries["report.json"] = json_bytes(report)
    extra = ("Le rapport joint a été sélectionné explicitement par l'auteur ; il peut contenir des noms ou chemins locaux.\n"
             if report_path is not None else "Aucun rapport, corpus, géométrie ni historique de mémoire séparé n'est joint.\n")
    destination = DESTINATIONS[kind]
    if kind == "experience":
        installation = """Le guide interactif se trouve normalement dans experience/model.json.
Ce ZIP sert à transporter et examiner son réseau. Ne pas installer ses poids
seuls dans un espace neuf pour poursuivre l'apprentissage : la rc5 demande les
observations des anciens groupes et signale un historique incomplet sinon.
Pour continuer ce guide, transférer séparément une copie cohérente de son
dossier experience complet (memory.json, events.jsonl, sources et modèles),
calculs arrêtés, après vérification du contenu et des droits de partage.
Ce paquet n'effectue pas cette opération et n'importe aucune mémoire.
"""
    else:
        installation = """Dans un espace de travail neuf, calculs arrêtés, copier model.json sous
le nom {destination}. Le chemin est relatif à l'espace de travail :
`python -m atelier serve --workspace DOSSIER` ou
`python -m atelier learn PIECE.json -o DOSSIER`.
Le guide de corpus est consulté par learn. Pour le guide historique, utiliser
`python -m atelier mesh PIECE.json -o DOSSIER --advisor CHEMIN/model.json`.
""".format(destination=destination)
    readme = """# Guide Atelier Mailleur partagé

Type : {kind}
Architecture : {inputs} → {hidden} → {outputs}, {parameters} paramètres.
Classement neuronal activé dans le fichier : {enabled}.

Installer une version compatible d'Atelier Mailleur et ses dépendances, puis
compiler le noyau pour son système si nécessaire. Ce paquet de poids ne contient
pas le programme ni Gmsh.

{installation}

Le noyau Rust vérifie encore le schéma et les conditions d'usage. Un guide
deploy_enabled=false reste désactivé. Ne pas modifier ce champ pour contourner
la validation. Essayer d'abord les pièces d'exemple et comparer qualité et temps
sur les propres pièces du destinataire. Aucun gain industriel n'est garanti.

manifest.json donne les SHA-256 et longueurs des autres fichiers du ZIP,
l'empreinte du fichier source et les champs retirés lors de la préparation.
Ces empreintes détectent une altération ; elles ne constituent pas une signature
d'identité ou une preuve de fiabilité de l'auteur. Vérifier un paquet reçu d'une
source connue avant de copier ses fichiers ; ce script n'importe pas d'archive.

{extra}
""".format(kind=kind, enabled=str(model["deploy_enabled"]).lower(), installation=installation,
           extra=extra, **architecture)
    entries["README.md"] = readme.encode("utf-8")
    manifest = {
        "schema": "atelier.shared_model.v1", "kind": kind,
        "model_schema": model["schema"], "feature_schema": model.get("feature_schema"),
        "architecture": architecture, "deploy_enabled": model["deploy_enabled"],
        "destination_relative_to_workspace": destination,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "omitted_model_fields": omitted,
        "checks": "Python format/dimensions/finite weights/declared activation evidence; Rust remains authoritative",
        "files": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for name, data in sorted(entries.items())},
    }
    output = Path(output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Le mode x refuse tout écrasement, y compris les liens symboliques.
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            archive.writestr(name, data)
        archive.writestr("manifest.json", json_bytes(manifest))
    return {"archive": str(output.resolve()), "bytes": output.stat().st_size,
            "deploy_enabled": model["deploy_enabled"], "architecture": architecture}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True, choices=sorted(DESTINATIONS))
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", type=Path, help="Rapport JSON choisi explicitement, joint tel qu'il est fourni")
    args = parser.parse_args()
    try:
        result = share_model(args.kind, args.model, args.output, args.report)
    except (OSError, ValueError, RecursionError) as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
