# Installation neuve de l’archive rc6 sur ce Mac

Campagne du 7 novembre 2026. Les deux environnements ci-dessous ont été créés depuis le **même ZIP original rc6**, avant les corrections rc7. Le code et les dépendances du projet courant n’ont pas servi à exécuter l’application dans ces copies.

- Archive : `Mailleur_rc6_formats_GPL_2026-11-07.zip`, **21 196 298 octets**.
- SHA256 : `4b87e592967a44915750beb8b99c2b54d5bcfce89667ef968666239cb3bb1864`.
- **1 456 empreintes** du `MANIFEST.sha256` vérifiées avant installation dans chaque copie.
- Machine réellement exécutée : **Mac arm64, macOS 27.2 / Darwin 27.2.0**. Cette campagne n’est pas un essai Linux ou Windows.
- Deux extractions différentes sous `/private/tmp`, deux interpréteurs téléchargés dans leurs dossiers temporaires, deux vrais `.venv` sans paquets système, deux dossiers Rust `target` neufs.
- Outil `uv 0.13.7`, Cargo/Rust 1.99.1 existants utilisés comme outils de construction ; le cache des sources Cargo a été réutilisé, aucun binaire `target` de l’installation habituelle.

## Résultats exécutés

Les compteurs sont **réussis / échecs / ignorés**. Les durées proviennent des sorties des suites, exécutées avec d’autres vérifications en parallèle ; ce ne sont pas des mesures de performance isolées.

| Python réel | NumPy résolu | Suite Python | Durée Python | Suite Rust release | Durée Rust | CLI / API / Qt |
| --- | --- | --- | --- | --- | --- | --- |
| 3.11.18 | 2.4.6 | 195 / 0 / 0 | 26.484 s | 121 / 0 / 0 | 6.12 s | Réussis |
| 3.13.18 | 2.5.4 | 195 / 0 / 0 | 28.737 s | 121 / 0 / 0 | 4.72 s | Réussis |

Dans chaque environnement, `scripts/install.py` livré dans le ZIP a créé le venv et installé les dépendances depuis `pyproject.toml`. `pip check`, `atelier doctor` et `atelier formats --json` ont réussi. Des assertions contrôlent `sys.prefix`, `sys.base_prefix`, le chemin du paquet `atelier`, les bibliothèques Python et la bibliothèque native Gmsh : ils appartiennent à la copie ou au venv attendu. Aucun `PYTHONPATH` vers le dossier de travail n’a été utilisé. `ATELIER_CORE` impose le noyau compilé à partir des sources extraites.

Autres versions effectivement installées dans les deux copies : Gmsh **4.15.2**, meshio **5.3.5**, h5py **3.16.0**, PySide6-Essentials et shiboken6 **6.12.0**. Les listes complètes sont dans `dependencies.txt`, et les versions/chemins dans `environment.json` de chaque campagne.

## Parcours du guide et des exemples

| Parcours réel, dans chaque copie | Résultat |
| --- | --- |
| Plaque `atelier_plate.json` → MSH, INP, Nastran, LS-DYNA, APDL structurel | 363 nœuds, 624 éléments, critères acceptés |
| `import_tetra.obj` → MSH, INP, MED, UNV, Cast3M, Nastran, LS-DYNA, APDL structurel | 132 nœuds, 330 éléments, critères acceptés |
| `import_tetra.3mf` → MSH et Nastran | 132 nœuds, 330 éléments, critères acceptés |
| API HTTP réelle sur boucle locale : info/formats, téléversement OBJ, travail natif, suivi, téléchargement BDF et ZIP | 330 éléments acceptés ; BDF contenant `GRID` et `CTETRA`, ZIP relu et vérifié |
| Qt hors écran : navigation des trois pages, source 3MF, calcul natif, choix Nastran, export via le traitement de l’interface | 330 éléments ; BDF produit ; capture `qt_offscreen.png` |

L’API est un vrai serveur HTTP à port éphémère ; le jeton de session n’est pas publié dans le résumé. Dans le parcours Qt, seule la sélection du dossier est automatisée par substitution de la boîte de dialogue ; le calcul et l’export utilisent le moteur réel. La capture de l’environnement 3.11 a été relue visuellement : les panneaux et le maillage s’affichent. Cela reste une **vérification automatisée hors écran**, pas une recette utilisateur complète avec les fenêtres natives et interactions physiques.

## Journaux et reproductibilité

- `python311/summary.json` : campagne complète Python 3.11.18.
- `python313_network/summary.json` : campagne complète Python 3.13.18 et recontrôle Qt.
- Chaque dossier conserve l’installation, la compilation, les suites complètes, le diagnostic, les versions, les fichiers CLI/API/Qt et les empreintes des preuves.
- `controller_self_checks.json` : huit vérifications ciblées du contrôleur (traversée de chemin, chemin Windows absolu, lien symbolique, collision de casse, manifeste erroné, extraction valide, avertissement Qt avant JSON, refus d’un dossier de preuves non vide), distinctes des 195 tests de l’archive.

Le contrôleur reproductible est `scripts/validate_clean_install.py`. Il demande un dossier de preuves vide, garde la copie temporaire pour inspection et ne modifie pas l’interpréteur global. Exemple avec des chemins adaptés à la machine :

```sh
python scripts/validate_clean_install.py \
  --archive /chemin/Mailleur_rc6_formats_GPL_2026-11-07.zip \
  --output verification/installation_neuve/python313 \
  --python 3.13 --uv /chemin/uv --cargo /chemin/cargo
```

Pour une chaîne Rust isolée, fournir aussi `--cargo-home` et `--rustup-home`. Une connexion réseau est nécessaire pour le Python temporaire et les dépendances initiales. Les calculs de cette campagne n’envoient pas de CAO à un service distant.

## Incidents du contrôleur, conservés

1. La tentative initiale `python313/` a échoué au téléchargement, avant installation, car l’accès DNS était bloqué. La relance réseau autorisée est `python313_network/`.
2. Le premier collecteur 3.13 avait terminé toutes les commandes avec le code zéro, mais attendait un JSON en première position dans la sortie Qt. Un avertissement de police précédait le JSON et a fait échouer **la collecte**, alors que le parcours Qt avait réussi. Le collecteur a été corrigé pour lire l’objet JSON final ; le parcours Qt a été exécuté de nouveau, dans la même copie isolée et dans un dossier de sortie neuf, avec succès. `summary_before_collector_fix.json` et les journaux antérieurs sont conservés. Une première tentative de recontrôle a rencontré le dossier d’export existant et s’est arrêtée ; le recontrôle final utilise `qt_recheck/`.
3. L’installateur rc6 affiche la dépréciation de la table `project.license` par setuptools. L’installation a réussi ; la correction SPDX est préparée séparément dans rc7. Voir la [documentation officielle setuptools](https://setuptools.pypa.io/en/stable/userguide/license_migration.html).

Ces résultats valident les parcours exécutés et leurs assertions. Ils ne contiennent pas les corrections rc7 en cours et ne certifient ni toutes les pièces, ni tous les formats, ni tous les logiciels depuis 2020. Aucun SOLIDWORKS réel, calcul de solveur externe, Windows ou Linux n’a été exécuté par **ce contrôleur d’installation Mac**. Les essais de solveurs et autres systèmes doivent être lus dans leurs propres campagnes.
