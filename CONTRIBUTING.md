# Contribuer à Mailleur

Lire le [guide d'utilisation et de développement](docs/UTILISATION_ET_DEVELOPPEMENT.md),
les [formats et leurs limites](docs/FORMATS.md) et les
[règles de contribution](docs/CONTRIBUER.md).

Depuis une extraction complète, avec Python 3.11 à 3.13 et Rust installés :

```sh
python scripts/install.py
cargo test --locked --manifest-path rust_core/Cargo.toml
python -m unittest discover -s tests -v
```

La dernière commande utilise le Python de l'environnement du projet : activer
`.venv` avant de l'exécuter, ou employer `.venv/bin/python` sous macOS/Linux et
`.venv\Scripts\python.exe` sous Windows. Les tests Qt utilisent le mode offscreen.

Un ajout de format doit préciser le lecteur ou l'écrivain, les types d'éléments,
les unités, les groupes transférés et les données non conservées. Joindre un
petit exemple public et des tests de relecture ou de contrat. Les solveurs et
convertisseurs propriétaires doivent être testés séparément des doubles de test.
La cible 2020+ ne constitue pas une certification universelle.

Une optimisation doit mesurer les mêmes données avant et après, en séparant
conversion, préparation, génération, qualité, fichiers et exports. Vérifier les
connectivités et décisions de qualité ; un rendu visuel identique ne suffit pas.

Ne joindre ni secrets de connexion, ni pièces dont la diffusion n'est pas
autorisée. Les détails de publication et de licence sont dans
[PUBLICATION.md](docs/PUBLICATION.md).
