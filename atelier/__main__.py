"""Commandes portables de l'Atelier Mailleur."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import webbrowser

from . import __version__
from .projects import normalize_project, strict_json


def positive_int(value):
    number = int(value)
    if number < 1: raise argparse.ArgumentTypeError("Entier strictement positif attendu")
    return number


def nonnegative_int(value):
    number = int(value)
    if number < 0: raise argparse.ArgumentTypeError("Entier positif ou nul attendu")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description="Atelier Mailleur — géométrie, maillage et échanges solveurs")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("desktop", help="ouvrir l'atelier Python/Qt à trois pages")
    serve = commands.add_parser("serve", help="ouvrir l'application locale")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--workspace", type=Path, default=Path.home() / "AtelierMailleur")
    serve.add_argument("--workers", type=positive_int, default=1)
    serve.add_argument("--timeout", type=positive_int, default=300)
    serve.add_argument("--no-browser", action="store_true")
    commands.add_parser("doctor", help="vérifier l'installation du moteur")
    formats_command = commands.add_parser("formats", help="lister les lecteurs, passerelles conditionnelles et exports")
    formats_command.add_argument("--json", action="store_true", help="sortie JSON (également utilisée par défaut)")
    cadpack = commands.add_parser("pack-cad", help="emballer un dossier CAO et ses références dans un fichier .cadpack")
    cadpack.add_argument("--directory", type=Path, required=True)
    cadpack.add_argument("--source", type=Path, required=True, help="source principale dans le dossier choisi")
    cadpack.add_argument("--output", type=Path, required=True)
    mesh = commands.add_parser("mesh", help="mailler un projet JSON ou portable .amp")
    mesh.add_argument("project", type=Path)
    mesh.add_argument("-o", "--output", type=Path, required=True)
    mesh.add_argument("--source", type=Path, help="source CAO/maillage ; atelier formats décrit les formats intégrés et conditionnels")
    mesh.add_argument("--formats", default="msh,med,inp,vtu,unv,ansys,cast3m,nastran,lsdyna,ansys_structural")
    mesh.add_argument("--advisor", type=Path, help="guide préalablement entraîné")
    train = commands.add_parser("train", help="évaluer les stratégies et entraîner le guide expérimental")
    train.add_argument("-o", "--output", type=Path, default=Path("output/apprentissage"))
    train.add_argument("--epochs", type=positive_int, default=200)
    train.add_argument("--seed", type=int, default=42)
    learn = commands.add_parser("learn", help="apprendre sur cette pièce, rechercher et mémoriser une discrétisation validée")
    learn.add_argument("project", type=Path)
    learn.add_argument("-o", "--output", type=Path, required=True)
    learn.add_argument("--source", type=Path)
    learn.add_argument("--memory", type=Path, help="mémoire partagée entre projets ; défaut : DOSSIER/experience")
    learn.add_argument("--max-trials", type=positive_int, default=12)
    learn.add_argument("--epochs", type=positive_int, default=100)
    learn.add_argument("--seed", type=int, default=42)
    learn.add_argument("--max-seconds", type=positive_int, default=240)
    learn.add_argument("--no-memory", action="store_true", help="isoler cet essai sans modifier la mémoire de l'atelier")
    learn.add_argument("--formats", default="msh")
    memory = commands.add_parser("memory", help="afficher l'historique et l'état de l'apprentissage continu")
    memory.add_argument("--workspace", type=Path, default=Path.home() / "AtelierMailleur")
    memory.add_argument("--memory", type=Path)
    corpus = commands.add_parser("corpus", help="planifier, produire et entraîner un corpus par lots dans Rust")
    corpus_commands = corpus.add_subparsers(dest="corpus_command", required=True)
    for name, help_text in (("plan", "planifier le corpus sans calculer les maillages"),
                            ("status", "lire l'avancement et les compteurs"),
                            ("preview", "examiner des configurations sans les mailler"),
                            ("run", "produire un lot borné et reprendre ensuite"),
                            ("train", "entraîner en parcourant les segments par mini-lots")):
        command = corpus_commands.add_parser(name, help=help_text)
        command.add_argument("--directory", type=Path, required=True)
        command.add_argument("--workspace", type=Path, default=Path.home() / "AtelierMailleur")
        if name == "plan":
            command.add_argument("--structured", type=nonnegative_int, default=250000)
            command.add_argument("--random", type=nonnegative_int, default=750000)
            command.add_argument("--seed", type=nonnegative_int, default=42)
            command.add_argument("--strategies", type=positive_int, default=4)
            command.add_argument("--shard-size", type=positive_int, default=1000)
        elif name == "preview":
            command.add_argument("--start-index", type=nonnegative_int, default=0)
            command.add_argument("--limit", type=positive_int, default=20)
        elif name == "status":
            command.add_argument("--adopt-legacy-integrity", action="store_true",
                                 help="après sauvegarde, adopter les octets actuels d'un ancien corpus sans prouver son intégrité passée")
        elif name == "run":
            command.add_argument("--limit", type=positive_int, default=20)
            command.add_argument("--max-seconds", type=positive_int, default=240)
            command.add_argument("--retain-meshes", action="store_true")
        elif name == "train":
            command.add_argument("--epochs", type=positive_int, default=3)
            command.add_argument("--batch-size", type=positive_int, default=128)
            command.add_argument("--max-records", type=nonnegative_int, default=10000,
                                 help="nombre maximal de cas par parcours ; 0 pour tous les segments")
            command.add_argument("--max-seconds", type=positive_int, default=240)
    args = parser.parse_args(argv)
    try:
        if args.command == "formats":
            from .imports import import_capabilities
            from .formats import FORMATS
            print(json.dumps({"version": __version__, "imports": import_capabilities(), "exports": FORMATS}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "pack-cad":
            from .imports import create_cadpack
            print(json.dumps(create_cadpack(args.source, args.directory, args.output), ensure_ascii=False, indent=2))
            return 0
        if args.command == "desktop":
            from .desktop import main as desktop_main
            return desktop_main([])
        if args.command == "doctor":
            from .server import backend_info
            from .native import core_path, gmsh_library
            native = {"available": False}
            try:
                import subprocess
                binary = core_path()
                version = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=10, check=True)
                native = {"available": True, "path": str(binary), "version": version.stdout.strip(), "gmsh_library": str(gmsh_library())}
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                native["error"] = str(error)
            from .imports import import_capabilities
            report = {"atelier": __version__, "python": sys.version.split()[0], "native_kernel": native,
                      "backend": backend_info(), "import_formats": import_capabilities()}
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if native["available"] and report["backend"]["available"] else 2
        if args.command == "serve":
            from .server import create_server
            server = create_server(args.host, args.port, args.workspace, args.workers, args.timeout)
            address = server.server_address
            display_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
            url = f"http://{display_host}:{address[1]}"
            print(f"Atelier Mailleur {__version__}\nApplication : {url}\nProjets et calculs : {args.workspace.expanduser().resolve()}\nArrêter avec Ctrl+C.", flush=True)
            if not args.no_browser: webbrowser.open(url)
            try: server.serve_forever(poll_interval=.2)
            except KeyboardInterrupt: pass
            finally: server.server_close()
            return 0
        if args.command == "train":
            from .native import NativeSession
            import shutil
            args.output.mkdir(parents=True, exist_ok=True)
            result = NativeSession(args.output).train(epochs=args.epochs, seed=args.seed)
            report = result["report"]
            for filename in ("guide.json", "report.json"):
                shutil.copyfile(Path(result["directory"]) / filename, args.output / filename)
            print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
            return 0
        if args.command == "memory":
            from .native import NativeSession
            print(json.dumps(NativeSession(args.workspace).memory_info(args.memory), ensure_ascii=False, indent=2))
            return 0
        if args.command == "corpus":
            from .native import NativeSession
            session = NativeSession(args.workspace, timeout=max(300, getattr(args, "max_seconds", 240) + 30))
            if args.corpus_command == "plan":
                report = session.corpus_plan(args.directory, structured=args.structured, random=args.random,
                                            seed=args.seed, strategies=args.strategies, shard_size=args.shard_size)
            elif args.corpus_command == "status":
                report = session.corpus_status(args.directory,adopt_legacy_integrity=args.adopt_legacy_integrity)
            elif args.corpus_command == "preview":
                report = session.corpus_preview(args.directory, start_index=args.start_index, limit=args.limit)
            elif args.corpus_command == "run":
                report = session.corpus_run(args.directory, limit=args.limit, max_seconds=args.max_seconds,
                                           retain_meshes=args.retain_meshes)
            else:
                report = session.corpus_train(args.directory, epochs=args.epochs, batch_size=args.batch_size,
                                             max_records=args.max_records, max_seconds=args.max_seconds)
            print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
            return 0
        from .native import NativeSession
        from .projects import load_portable_project
        directory = args.output.expanduser().resolve()
        source = args.source.resolve() if args.source else None
        if args.project.suffix.lower() == ".amp":
            import uuid
            if source: raise ValueError("La source est déjà intégrée au projet portable")
            loaded = load_portable_project(args.project.read_bytes(), directory / "sources" / uuid.uuid4().hex)
            project, source = loaded["project"], loaded["source"]
        else:
            project = normalize_project(strict_json(args.project.read_bytes()))
        formats = [name.strip() for name in args.formats.split(",") if name.strip()]
        session = NativeSession(directory, timeout=max(300, args.max_seconds + 30) if args.command == "learn" else 300)
        if getattr(args, "advisor", None):
            import shutil
            model = directory / "models" / "guide.json"
            model.parent.mkdir(parents=True, exist_ok=True)
            if args.advisor.resolve() != model.resolve(): shutil.copyfile(args.advisor, model)
        if args.command == "learn":
            result = session.learn(project, source=source, max_trials=args.max_trials, epochs=args.epochs,
                                   seed=args.seed, max_seconds=args.max_seconds, use_memory=not args.no_memory,
                                   memory_dir=args.memory)
            project = result["report"]["selected_project"]
        else:
            result = session.generate(project, source=source, use_advisor=bool(args.advisor))
        outputs = []
        unit = project.get("units", {}).get("input", "mm")
        for format_name in formats:
            outputs.append(session.export(Path(result["directory"]), directory / "exports", format_name,
                                          units=unit, planar=project["mode"] == "planar"))
        result["exports"] = outputs
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except (ValueError, OSError, ImportError, RuntimeError) as error:
        print(f"Erreur : {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
