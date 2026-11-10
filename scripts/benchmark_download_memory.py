"""Mesurer la RAM du transfert fichier, sans réseau ni calcul de maillage."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "verification/local_m3/download_memory.json")
    args = parser.parse_args()
    program = '''
import json,os,resource,sys
from pathlib import Path
from atelier.server import Handler
path=Path(sys.argv[2])
with open(os.devnull,'wb') as stream:
    if sys.argv[1]=='baseline':
        payload=path.read_bytes();stream.write(payload)
    else:
        handler=object.__new__(Handler)
        handler.wfile=stream
        handler._headers=lambda *args:None
        handler._send_file(path)
rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(json.dumps({'max_rss_bytes':rss if sys.platform=='darwin' else rss*1024}))
'''
    with tempfile.TemporaryDirectory(prefix="mailleur-download-") as folder:
        path = Path(folder) / "fichier.bin"
        with path.open("wb") as stream:
            stream.truncate(128 * 1024 * 1024)
        report = {"scope": "Processus séparés, imports identiques, fichier creux128Mio -> /dev/null ; pic RSS du processus entier, sans réseau ni génération",
                  "bytes": path.stat().st_size, "python": sys.version.split()[0]}
        for mode in ("baseline", "streaming"):
            result = subprocess.run([sys.executable, "-c", program, mode, str(path)], cwd=ROOT, capture_output=True, text=True, check=True)
            report[mode] = json.loads(result.stdout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
