"""Vérifier une archive dans une copie et un environnement réellement neufs.

Ce contrôleur n'importe aucun module du projet courant. Il télécharge un Python
isolé avec uv, appelle l'installateur livré dans le ZIP et reconstruit le noyau.
Les caches Cargo peuvent être réutilisés, jamais le dossier target du projet.
Les tests Qt sont automatisés hors écran, pas une recette humaine de l'interface.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile


PYTHON_SUITE = r'''
import json, pathlib, sys, unittest
suite = unittest.defaultTestLoader.discover("tests")
result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
summary = {"tests_run": result.testsRun, "failures": len(result.failures),
           "errors": len(result.errors), "skipped": len(result.skipped),
           "skip_reasons": [{"test": str(t), "reason": r} for t,r in result.skipped],
           "expected_failures": len(result.expectedFailures),
           "unexpected_successes": len(result.unexpectedSuccesses)}
pathlib.Path(sys.argv[1]).write_text(json.dumps(summary, indent=2)+"\n")
raise SystemExit(not result.wasSuccessful())
'''

ENVIRONMENT_PROBE = r'''
import importlib.metadata, json, pathlib, platform, sys
import atelier, meshio, h5py, numpy, PySide6
from atelier.native import core_path, gmsh_library
root, old = pathlib.Path(sys.argv[1]).resolve(), pathlib.Path(sys.argv[2]).resolve()
expected = root/".venv"
assert pathlib.Path(sys.prefix).resolve() == expected.resolve()
assert sys.prefix != sys.base_prefix
assert pathlib.Path(atelier.__file__).resolve().is_relative_to(root)
assert not pathlib.Path(atelier.__file__).resolve().is_relative_to(old)
filename="mailleur-core.exe" if sys.platform=="win32" else "mailleur-core"
assert pathlib.Path(core_path()).resolve() == root/"rust_core/target/release"/filename
assert pathlib.Path(gmsh_library()).resolve().is_relative_to(expected)
for mod in (meshio, h5py, numpy, PySide6):
    assert pathlib.Path(mod.__file__).resolve().is_relative_to(expected), mod.__file__
assert not any(pathlib.Path(p).resolve().is_relative_to(old) for p in sys.path if p)
print(json.dumps({"python":sys.version, "executable":sys.executable,
 "prefix":sys.prefix,"base_prefix":sys.base_prefix,"platform":platform.platform(),
 "machine":platform.machine(),"atelier":atelier.__file__,"core":str(core_path()),
 "gmsh_library":str(gmsh_library()),"isolated_imports_verified":True,
 "dependencies":{n:importlib.metadata.version(n) for n in
 ("atelier-mailleur","gmsh","meshio","h5py","numpy","PySide6-Essentials","shiboken6")}},indent=2))
'''

API_SMOKE = r'''
import base64, io, json, pathlib, sys, threading, time, zipfile
from urllib.request import Request, urlopen
from atelier.server import create_server
root, proof = pathlib.Path.cwd(), pathlib.Path(sys.argv[1])
server = create_server("127.0.0.1",0,proof/"api_workspace",max_workers=1,timeout=60)
thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
base = "http://127.0.0.1:"+str(server.server_address[1])
token = None
def request(path,data=None):
    headers = {} if token is None else {"X-Atelier-Token":token}
    if data is not None: headers["Content-Type"]="application/json"
    req = Request(base+path,data=None if data is None else json.dumps(data).encode(),headers=headers)
    with urlopen(req,timeout=30) as response:
        payload=response.read()
        return json.loads(payload) if "application/json" in response.headers.get("Content-Type","") else payload
try:
    info=request("/api/info"); token=info["token"]
    assert info["backend"]["available"]
    formats=request("/api/formats")
    assert all(k in formats["exports"] for k in ("nastran","lsdyna","ansys_structural"))
    source=root/"examples/import_tetra.obj"
    upload=request("/api/uploads",{"filename":source.name,"content_base64":base64.b64encode(source.read_bytes()).decode()})
    project=json.loads((root/"examples/import_maillage.json").read_text())
    created=request("/api/jobs",{"project":project,"upload_id":upload["id"]})
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        job=request("/api/jobs/"+created["id"])
        if job["status"] in ("completed","failed","cancelled"): break
        time.sleep(.05)
    assert job["status"]=="completed",job
    assert job["report"]["elements"]>0
    assert job["report"]["quality"]["accepted"] is True
    bdf=request("/api/jobs/"+created["id"]+"/download?format=nastran")
    assert b"CTETRA" in bdf and b"GRID" in bdf
    (proof/"api_download.bdf").write_bytes(bdf)
    bundle=request("/api/jobs/"+created["id"]+"/bundle")
    with zipfile.ZipFile(io.BytesIO(bundle)) as z:
        assert z.testzip() is None and any(n.endswith(".msh") for n in z.namelist())
    (proof/"api_bundle.zip").write_bytes(bundle)
    print(json.dumps({"passed":True,"binding":"127.0.0.1 ephemeral port", "source":source.name,
     "elements":job["report"]["elements"],"nodes":job["report"]["nodes"],
     "quality_accepted":True,"bdf_bytes":len(bdf),"bundle_bytes":len(bundle),
     "external_solver_executed":False},indent=2))
finally:
    server.shutdown(); server.server_close(); server.manager.close(); thread.join(timeout=5)
'''

QT_SMOKE = r'''
import json, pathlib, sys, time
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QTabBar
from atelier.desktop import AtelierWindow
from atelier.native import NativeSession
root,proof=pathlib.Path.cwd(),pathlib.Path(sys.argv[1])
app=QApplication([])
class CheckedWindow(AtelierWindow):
    failures=[]
    exported=None
    def _calculation_failed(self,message):
        self.failures.append(str(message))
        self.set_status(str(message))
    def _export_succeeded(self,result):
        self.exported=result
        super()._export_succeeded(result)
window=CheckedWindow(workspace=proof/"qt_workspace",session=NativeSession(proof/"qt_workspace",timeout=60))
window.resize(1360,900); window.show(); app.processEvents()
def wait():
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        app.processEvents()
        thread=window.calculation_thread
        if thread is None or not thread.isRunning():
            app.processEvents(); break
        time.sleep(.01)
    else:
        window.cancel_calculation(); raise RuntimeError("Qt calculation timeout")
    assert not window.failures,window.failures
try:
    tabs=window.findChild(QTabBar,"workflowTabs")
    assert tabs.count()==3
    for i in range(3): tabs.setCurrentIndex(i);app.processEvents()
    project=json.loads((root/"examples/import_maillage.json").read_text())
    window.load_project(project,root/"examples/import_tetra.3mf")
    window.generate_mesh();wait()
    assert window.result and window.result["report"]["elements"]>0
    assert window.result["report"]["quality"]["accepted"] is True
    assert window.format.findData("nastran")>=0
    window.format.setCurrentIndex(window.format.findData("nastran"))
    destination=proof/"qt_exports";destination.mkdir()
    with patch("atelier.desktop.QFileDialog.getExistingDirectory",return_value=str(destination)):
        window.export_mesh();wait()
    assert window.exported is not None
    files=list(destination.glob("*.bdf"));assert len(files)==1 and b"CTETRA" in files[0].read_bytes()
    screenshot=window.grab();assert screenshot.save(str(proof/"qt_offscreen.png"))
    print(json.dumps({"passed":True,"platform":"offscreen","source":"import_tetra.3mf",
     "tabs_navigated":3,"real_native_calculation":True,"native_elements":window.result["report"]["elements"],
     "native_nodes":window.result["report"]["nodes"],"export":"nastran", "bdf_bytes":files[0].stat().st_size,
     "file_dialog_automated":True,"human_visual_acceptance":False},indent=2))
finally:
    if window.calculation_thread and window.calculation_thread.isRunning():
        window.cancel_calculation();window.calculation_thread.wait(10000)
    window.close();app.processEvents()
'''


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def sha256(path: Path):
    digest=hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda:file.read(1024*1024),b""): digest.update(chunk)
    return digest.hexdigest()


def final_json(text: str):
    """Les avertissements Qt sur stderr peuvent précéder le résultat JSON."""
    decoder=json.JSONDecoder()
    for match in re.finditer(r"(?m)^\{",text):
        try:
            value,end=decoder.raw_decode(text,match.start())
        except json.JSONDecodeError:
            continue
        if not text[end:].strip(): return value
    raise ValueError("Résultat JSON final absent du journal")


def extract_checked(archive: Path, destination: Path):
    """Ne pas autoriser qu'un ZIP de test écrive hors de sa copie temporaire."""
    with zipfile.ZipFile(archive) as zipped:
        seen=set()
        for info in zipped.infolist():
            part=PurePosixPath(info.filename)
            key=info.filename.casefold()
            if (part.is_absolute() or ".." in part.parts or "\\" in info.filename
                    or not part.parts or ":" in part.parts[0] or key in seen
                    or stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError(f"Entrée ZIP non sûre ou ambiguë : {info.filename!r}")
            seen.add(key)
        if zipped.testzip() is not None: raise ValueError("ZIP corrompu")
        zipped.extractall(destination)
    roots=list(destination.glob("*/pyproject.toml"))
    if len(roots)!=1: raise ValueError("Une seule racine projet attendue")
    root=roots[0].parent
    for forbidden in (".venv",".runtime",".toolchain",".bootstrap","rust_core/target"):
        if (root/forbidden).exists(): raise ValueError(f"État local embarqué : {forbidden}")
    manifest=root/"MANIFEST.sha256"
    entries=0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest,name=line.split("  ",1)
        path=root/PurePosixPath(name)
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError("Chemin de manifeste invalide")
        if sha256(path)!=digest: raise ValueError(f"Empreinte différente : {name}")
        entries+=1
    return root,entries


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--python",default="3.13",choices=("3.11","3.12","3.13"))
    parser.add_argument("--uv",type=Path,required=True)
    parser.add_argument("--cargo",type=Path,required=True)
    parser.add_argument("--cargo-home",type=Path)
    parser.add_argument("--rustup-home",type=Path)
    parser.add_argument("--scratch-parent",type=Path,
                        default=Path("/private/tmp") if sys.platform=="darwin" else Path(tempfile.gettempdir()))
    args=parser.parse_args(argv)
    archive=args.archive.expanduser().resolve(); output=args.output.expanduser().resolve()
    if output.exists() and any(output.iterdir()): parser.error("Utiliser un dossier de preuves neuf et vide")
    output.mkdir(parents=True,exist_ok=True)
    scratch=Path(tempfile.mkdtemp(prefix="mailleur-clean-install-",dir=args.scratch_parent)).resolve()
    summary={"schema":"atelier.clean_install_validation.v1","started_utc":datetime.now(timezone.utc).isoformat(),
             "archive":str(archive),"archive_bytes":archive.stat().st_size,"archive_sha256":sha256(archive),
             "host":{"system":platform.system(),"release":platform.release(),"machine":platform.machine(),
                     "mac_ver":platform.mac_ver()[0]},"scratch":str(scratch),"requested_python":args.python,
             "fresh_virtualenv":True,"fresh_rust_target":True,"steps":[],"passed":False,
             "limits":["Ce résultat concerne exclusivement le système réellement exécuté.",
                       "Qt hors écran automatisé ; ce n'est pas une recette visuelle humaine.",
                       "Aucun SOLIDWORKS réel ni solveur externe n'est exécuté par ce parcours."]}
    env=os.environ.copy()
    for key in list(env):
        if (key.startswith(("PYTHON","PIP_","UV_")) or key in
                ("VIRTUAL_ENV","ATELIER_CORE","GMSH_LIBRARY","MAILLEUR_CAD_CONVERTERS","CARGO_TARGET_DIR")):
            env.pop(key,None)
    env.update(PYTHONNOUSERSITE="1",PIP_CONFIG_FILE=os.devnull,PIP_DISABLE_PIP_VERSION_CHECK="1",
               PIP_CACHE_DIR=str(scratch/"pip-cache"),UV_CACHE_DIR=str(scratch/"uv-cache"),
               UV_PYTHON_INSTALL_DIR=str(scratch/"python"),QT_QPA_PLATFORM="offscreen",
               CARGO_TARGET_DIR=str(scratch/"unassigned-target"))
    cargo=args.cargo.expanduser().absolute()
    env["PATH"]=str(cargo.parent)+os.pathsep+os.defpath
    if args.cargo_home: env["CARGO_HOME"]=str(args.cargo_home.expanduser().resolve())
    if args.rustup_home: env["RUSTUP_HOME"]=str(args.rustup_home.expanduser().resolve())
    def run(label,command,cwd,timeout=600,json_result=False):
        log=output/(label+".txt");started=time.monotonic()
        item={"name":label,"command":[str(x) for x in command],"cwd":str(cwd),"log":log.name}
        summary["steps"].append(item)
        print(f"[{label}] démarrage",flush=True)
        try:
            with log.open("w",encoding="utf-8") as stream:
                process=subprocess.run([str(x) for x in command],cwd=cwd,env=env,stdout=stream,
                                       stderr=subprocess.STDOUT,timeout=timeout,check=False)
            item.update(returncode=process.returncode,seconds=round(time.monotonic()-started,3))
            if process.returncode: raise RuntimeError(f"{label}: code {process.returncode}; voir {log}")
            if json_result:
                value=final_json(log.read_text(encoding="utf-8"));write_json(output/(label+".json"),value)
                item["result"]=value
            print(f"[{label}] réussi ({item['seconds']} s)",flush=True)
        finally:
            write_json(output/"summary.json",summary)
        return log
    try:
        root,count=extract_checked(archive,scratch/"extracted")
        summary.update(extracted_root=str(root),archive_manifest_verified_files=count)
        env["CARGO_TARGET_DIR"]=str(root/"rust_core/target")
        core=root/"rust_core/target/release"/("mailleur-core.exe" if sys.platform=="win32" else "mailleur-core")
        env["ATELIER_CORE"]=str(core)
        run("uv_version",[args.uv,"--version"],scratch)
        run("python_download",[args.uv,"--no-config","python","install",args.python,"--no-bin","--no-registry"],scratch)
        interpreters=sorted((scratch/"python").glob(f"cpython-{args.python}.*/*/python3"))
        if not interpreters:
            interpreters=sorted((scratch/"python").glob(f"cpython-{args.python}.*/bin/python3"))
        if len(interpreters)!=1: raise RuntimeError(f"Interpréteur isolé ambigu : {interpreters}")
        python=interpreters[0]
        run("python_version",[python,"--version"],root)
        run("rust_version",[cargo,"--version"],root)
        run("rustc_version",[cargo.parent/"rustc","--version","--verbose"],root)
        run("fresh_rust_build",[cargo,"build","--locked","--release","--manifest-path","rust_core/Cargo.toml"],root)
        summary["fresh_core_sha256"]=sha256(core)
        run("guide_install",[python,"scripts/install.py"],root,timeout=1200)
        vpython=root/".venv"/("Scripts/python.exe" if sys.platform=="win32" else "bin/python")
        env["PATH"]=str(vpython.parent)+os.pathsep+env["PATH"]
        env["VIRTUAL_ENV"]=str(root/".venv")
        run("environment",[vpython,"-c",ENVIRONMENT_PROBE,str(root),str(Path(__file__).resolve().parents[1])],root,json_result=True)
        run("pip_check",[vpython,"-m","pip","check"],root)
        run("dependencies",[vpython,"-m","pip","freeze"],root)
        run("doctor",[vpython,"-m","atelier","doctor"],root,json_result=True)
        run("formats",[vpython,"-m","atelier","formats","--json"],root,json_result=True)
        run("rust_tests",[cargo,"test","--locked","--release","--manifest-path","rust_core/Cargo.toml"],root)
        rust_log=(output/"rust_tests.txt").read_text(encoding="utf-8")
        counts=re.findall(r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored",rust_log)
        if not counts: raise RuntimeError("Compteurs Rust absents")
        summary["rust_suite"]={"passed":sum(int(c[0]) for c in counts),"failed":sum(int(c[1]) for c in counts),
                               "ignored":sum(int(c[2]) for c in counts)}
        run("python_tests",[vpython,"-c",PYTHON_SUITE,str(output/"python_suite.json")],root)
        summary["python_suite"]=json.loads((output/"python_suite.json").read_text())
        cli_cases=[("plate",["examples/atelier_plate.json"],"msh,inp,nastran,lsdyna,ansys_structural"),
                   ("obj",["examples/import_maillage.json","--source","examples/import_tetra.obj"],
                    "msh,inp,med,unv,cast3m,nastran,lsdyna,ansys_structural"),
                   ("3mf",["examples/import_maillage.json","--source","examples/import_tetra.3mf"],"msh,nastran")]
        summary["cli_examples"]=[]
        for name,inputs,formats in cli_cases:
            log=run("cli_"+name,[vpython,"-m","atelier","mesh",*inputs,"-o",str(output/("cli_"+name)),"--formats",formats],root)
            result=json.loads(log.read_text())
            assert result["report"]["elements"]>0 and result["report"]["quality"]["accepted"] is True
            summary["cli_examples"].append({"name":name,"elements":result["report"]["elements"],
                 "nodes":result["report"]["nodes"],"formats":formats.split(","),"quality_accepted":True})
        run("api_smoke",[vpython,"-c",API_SMOKE,str(output)],root,json_result=True)
        run("qt_smoke",[vpython,"-c",QT_SMOKE,str(output)],root,json_result=True)
        summary["passed"]=True
    except Exception as error:
        summary["error"]={"type":type(error).__name__,"message":str(error)}
        print(str(error),file=sys.stderr)
    finally:
        summary["finished_utc"]=datetime.now(timezone.utc).isoformat()
        summary["proof_files"]={p.name:{"bytes":p.stat().st_size,"sha256":sha256(p)}
                                for p in sorted(output.iterdir()) if p.is_file() and p.name!="summary.json"}
        write_json(output/"summary.json",summary)
    print(json.dumps({"passed":summary["passed"],"summary":str(output/"summary.json"),"scratch":str(scratch)},indent=2))
    return 0 if summary["passed"] else 1


if __name__=="__main__":
    raise SystemExit(main())
