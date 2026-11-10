#!/bin/sh
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Python 3.11, 3.12 ou 3.13 est nécessaire.'
    printf '%s\n' 'Installer Python puis relancer ce fichier.'
    read -r ATELIER_ATTENTE
    exit 2
fi
python3 scripts/launch.py "$@"
ATELIER_CODE=$?
if [ "$ATELIER_CODE" -ne 0 ] && [ "$ATELIER_CODE" -ne 130 ]; then
    printf '%s\n' 'Le lancement a échoué. Consulter le message ci-dessus puis README.md.'
    read -r ATELIER_ATTENTE
fi
exit "$ATELIER_CODE"
