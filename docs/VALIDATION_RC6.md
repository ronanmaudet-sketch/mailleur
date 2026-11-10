# Validation de la rc6 — 7 novembre 2026

## Résultats effectivement obtenus

Cette révision a été compilée et testée sur **MacBook Pro Apple M3, 16 Go,
macOS 27.0, arm64**. Python 3.12.16, Rust 1.99.1 et Gmsh 4.15.2 sont utilisés.
Les dépendances Python de calcul restent celles de la préparation M3 ; aucun
SDK CAO propriétaire n'est installé ni redistribué dans cette livraison.

| Vérification | Résultat observé |
| --- | --- |
| Suite Python complète | **195 tests réussis en 25,242 s**, aucun échec ni test ignoré |
| Suite Rust complète, profil release | **121 tests réussis en 4,89 s**, aucun échec ni test ignoré |
| Compilation Rust | Release native Apple M3, Cargo.lock utilisé, version `1.0.0-rc.6` |
| Diagnostic et dépendances | Noyau et Gmsh trouvés ; `pip check` sans dépendance cassée |
| Passerelle SOLIDWORKS, doubles de test | **9 scénarios réussis** avec PowerShell 7.6.6 portable sur Mac |
| Conversion avec SOLIDWORKS installé | **Non exécutée** ; Windows et SOLIDWORKS requis |
| Calculs dans Abaqus, Cast3M, ANSYS, Nastran ou LS-DYNA | **Non exécutés** ; aucun résultat solveur n'est revendiqué |

Les durées des suites n'incluent pas la compilation. Elles ne mesurent pas un
calcul industriel, une traduction CAO ou la qualité d'une solution mécanique.
La matrice CI proposée pour Linux/macOS/Windows n'a pas été exécutée à distance
pendant cette préparation locale.

Les journaux complets et les versions sont dans
`verification/rc6_20261107/`. Le fichier `summary.json` en donne le bilan
structuré. Les rapports du 4 novembre restent dans leurs dossiers d'origine ;
ils ne sont pas utilisés comme preuve de ces nouveaux formats.

## Imports de fichiers réellement exercés

Les nouveaux tests utilisent de petites géométries publiques construites dans
un dossier temporaire. Ils demandent un véritable nouveau maillage au noyau
Rust/Gmsh, vérifient l'admissibilité et contrôlent que l'original est inchangé.

| Entrée | Parcours contrôlé |
| --- | --- |
| OBJ, PLY, OFF | Lecture d'une peau tétraédrique et génération volumique ; quadrilatère OBJ remaillé en 2D |
| VTU, VTK, MSH | Lecture de cellules volumiques, extraction de leur peau et nouveau maillage |
| Abaqus INP, MED, Nastran BDF | Lecture de maillages tétraédriques, reconstruction et génération |
| Medit MESH, Netgen VOL | Lecture de cellules tétraédriques, reconstruction et génération |
| UNV volumique | Lecture Gmsh native, extraction de peau et nouveau maillage admissible |
| 3MF Core | Objets, composants internes, unités et chaîne de transformations ; vérification des positions dans le résultat natif |
| Entrée quadratique VTU | Reconstruction par les sommets principaux, approximation explicitement signalée |
| STEP et STL | Régressions existantes, unités 3D, transformations et vérifications du parcours natif |

L'essai API transmet aussi un OBJ par upload HTTP, lance sa reconstruction,
attend le travail réel et télécharge un BDF contenant des tétraèdres.
Le catalogue des formats est testé sur `/api/formats` et `/api/info`.

Les cas de refus vérifiés couvrent les références XML ou fichiers externes
interdits, les graphes 3MF cycliques ou vides, les extensions requises non
prises en charge, les liens HDF5 et les commandes INCLUDE externes.
Les conteneurs CADPACK sont vérifiés pour les chemins, liens, collisions de
casse, noms non portables, fichiers absents et empreintes altérées.

Le noyau teste connectivités, coordonnées non finies, faces internes, cellules
dégénérées, orientation et topologies non manifold. Les objets 3MF dont les
boîtes se touchent ou se chevauchent sont refusés conservativement. Ce contrôle
peut rejeter des formes pourtant séparées ; il n'effectue pas d'union booléenne.

## Exports et vérifications

Les exports historiques restent dans la suite. Les trois nouveaux profils
sont testés séparément, avec refus des types qu'ils ne représentent pas.

| Profil ajouté | Contrôles réalisés |
| --- | --- |
| `nastran` / BDF | Cartes documentées, relecture meshio, numérotation des nœuds quadratiques, précision des coordonnées, ensembles et correspondances de noms |
| `lsdyna` / KEY | Nœuds, cellules linéaires et répétitions de nœuds selon forme ; relecture du sous-ensemble écrit et ensembles ; refus des quadratiques sans réduction d'ordre |
| `ansys_structural` / APDL | Types SOLID285, SOLID187, SOLID185, PLANE182 et SHELL181 selon cellule/mode ; connectivité et composants ; refus des formes non prises en charge |

La relecture d'un sous-ensemble KEY par le lecteur de contrôle fourni n'est
pas une validation par LS-DYNA. Un script APDL contrôlé structurellement n'est
pas un calcul exécuté dans ANSYS. Une relecture BDF préserve des données de
maillage, sans prouver qu'un jeu de cartes complet possède les propriétés,
matériaux et conditions nécessaires à une analyse Nastran.

Le bundle de tous les profils est testé sur un volume linéaire. Sur un type
non accepté, un format peut apparaître en erreur dans le manifeste du bundle,
tandis que les autres exports restent disponibles. Par exemple, LS-DYNA ne
reçoit pas une réduction silencieuse des tétraèdres quadratiques.

## Annulation, délai et intégrité des sessions

Les essais utilisent aussi de vrais processus de test qui ignorent SIGTERM.
Ils contrôlent l'arrêt des descendants après annulation et après un timeout
interne du convertisseur, même si le processus principal s'est déjà terminé.
L'API nettoie le groupe qu'elle possède à la fin de chaque travail.

Une requête concurrente ne peut pas réinitialiser l'annulation active.
L'opération partage une échéance entre lecture, normalisation et maillage.
Une source dépassant 200 Mio est refusée avant copie ; l'API conserve sa limite
d'upload distincte de 50 Mio. Un fichier préparé altéré n'est pas réutilisé pour
un remaillage local. Ces limites ne constituent pas une borne absolue de RAM
pour chaque bibliothèque et chaque fichier compressé.

## Sens de la cible 2020+

Le projet vise des échanges avec les versions 2020 et ultérieures à travers
des formats et types documentés. **Il n'est pas certifié pour tous les
logiciels, toutes les variantes et toutes les versions depuis 2020.** Aucun
test local de fichier ne peut remplacer cette matrice d'essais externes.

Les formats natifs restent conditionnés à une passerelle adaptée au fichier.
Les tests SOLIDWORKS simulent les API ; ils ne prouvent pas que SOLIDWORKS 2020
lit un assemblage créé dans une version plus récente. Une installation réelle
doit encore vérifier la résolution des composants et la géométrie STEP obtenue.

Pour contribuer à cette validation, partir d'une petite pièce partageable,
noter les versions exactes, contrôler nombres d'éléments, connectivités,
groupes, unités et orientation dans le logiciel cible, puis effectuer un petit
calcul pertinent. Conserver les refus et différences avec les réussites.
Un futur résultat de logiciel cible doit compléter ces preuves, pas être
déduit de son extension de fichier.

## Commandes de reproduction

Après installation, utiliser le Python de `.venv` et une chaîne Cargo accessible :

```sh
cargo test --locked --release --manifest-path rust_core/Cargo.toml
python -m unittest discover -s tests -v
python -m atelier doctor
python -m atelier formats --json
```

Pour les contrôles de protocole sans SOLIDWORKS :

```sh
pwsh -NoProfile -NonInteractive -File scripts/converters/test_solidworks_bridge.ps1
```

Sous Windows PowerShell, remplacer `pwsh` par `powershell.exe`. Cette commande
de test ne demande pas COM ; le script de conversion réel demande une session
Windows STA et l'installation SOLIDWORKS décrites dans `IMPORTS_NATIFS.md`.
