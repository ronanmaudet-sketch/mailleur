"""Deprecated command spelling, forwarding to the Rust-backed atelier CLI."""
from __future__ import annotations

import sys


def main(argv=None):
    from atelier.__main__ import main as atelier_main
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in ("benchmark", "inspect"):
        print("Cette commande du prototype Python a été retirée. Utiliser atelier-mailleur "
              "et ses projets déclaratifs, son apprentissage et ses corpus Rust.", file=sys.stderr)
        return 2
    print("La commande mailleur redirige désormais vers atelier-mailleur (noyau Rust).", file=sys.stderr)
    return atelier_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
