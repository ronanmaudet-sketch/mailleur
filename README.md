# Atelier Mailleur — 1.0.0rc10

Le moteur de référence est celui du projet **« Optimiser le mailleur »**. La version publique rc10 reprend le générateur structuré écrit en Rust de l’archive du 19 novembre 2026 (rc6 dans le projet d’origine). En mode automatique, il construit des **quadrilatères en 2D et des hexaèdres en 3D**, d’ordre 2 par défaut, sur les géométries compatibles. Le rapport indique comment les cellules ont été générées. Les formes explicites restent disponibles pour les essais manuels.

[Télécharger les sources](https://github.com/ronanmaudet-sketch/mailleur/archive/refs/heads/main.zip) · [Installer et lancer](#installation-et-lancement) · [Exemple 2D](examples/direct_quad.json) · [Exemple 3D](examples/direct_hex.json) · [API Python](docs/API_ET_COUPLAGE_SOLVEURS.md) · [Préparer un STEP depuis SolidWorks](docs/IMPORTS_NATIFS.md) · [Essai Cast3M](examples/cast3m_pilot/README.md) · [Vérifications de la rc10](verification/rc10/README.md)

Sur un bloc rectangulaire, le noyau construit la grille et place les nœuds d’ordre 2 directement. Il sait aussi traiter certaines plaques percées et des extrusions. Si une CAO importée exige un découpage en blocs qu’il ne sait pas faire, le parcours automatique signale cette limite. Pour lancer un premier calcul depuis le dossier installé :

```sh
python -m atelier mesh examples/direct_hex.json -o output/bloc
```

Les mesures de la [rc9 par subdivision](https://github.com/ronanmaudet-sketch/mailleur/tree/v1.0.0-rc9) et les rapports Linux de la rc6 amont ci-dessous identifient leurs propres versions. Les capacités d’import et d’export de la version directe figurent dans son [guide des limites](docs/LIMITES.md). Le code du projet est distribué sous [GPLv3 ou ultérieure](LICENSE).


Application locale de préparation CAO et de maillage, avec une **interface de
bureau PySide6 en Python** et un **noyau numérique natif en Rust**. Le noyau
appelle Gmsh/OpenCASCADE par son interface C : les calculs de cette bibliothèque
restent exécutés par ses composants natifs C/C++.

L'interface suit trois pages : **Géométrie**, **Maillage** et **Export**. Elle
permet de préparer les unités, sélectionner des entités, régler le maillage
local et choisir un profil d'export. Les anciens noms de modules Python servent
à la compatibilité ou signalent les anciennes API retirées ; ils ne fournissent
plus de moteur numérique alternatif.

`rc6` désigne une version candidate. Les cas réellement vérifiés sont consignés
dans les rapports de livraison ; la disponibilité d'une commande ne prouve pas
que toutes les géométries défectueuses ou toutes les versions de solveurs ont
été validées. Voir [les limites](docs/LIMITES.md) et
[l'architecture](docs/ARCHITECTURE.md).

## Éléments directs et API de la rc6

Les nouveaux projets utilisent l'**ordre 2** par défaut. Les quadrilatères,
hexaèdres, prismes et pyramides peuvent être générés directement en Rust sur
les blocs paramétriques compatibles, sans recombinaison et sans remplacement
silencieux de la famille demandée. La portée actuelle comprend notamment les
plaques simples, boîtes, quadrilatères convexes et une plaque à un trou
circulaire avec grille en anneau. Elle ne fournit pas encore un découpage
automatique de toutes les CAO complexes en blocs hexaédriques.

Le [guide des évolutions rc6](docs/EVOLUTIONS_RC6.md) décrit les géométries,
les divisions, les progressions de tailles et les variantes quadratiques.
L'[API de scripts et de couplage solveurs](docs/API_ET_COUPLAGE_SOLVEURS.md)
permet de générer et d'exporter un maillage, puis de piloter ses relances à
partir des résultats du solveur choisi. Les échanges sont génériques ;
les adaptateurs de calcul et le transfert des champs physiques doivent être
validés avec le logiciel destinataire.

## Optimisations de la rc5

La rc5 réduit les traitements répétés dans l'entraînement, la reprise des
campagnes et les contrôles de qualité. La vue conserve les données de rendu
qui restent valides au lieu de les reconstruire à chaque rafraîchissement.
Les [optimisations et leur protocole de mesure](docs/OPTIMISATIONS_RC5.md)
distinguent les temps d'exécution des résultats de qualité du maillage.
Sur une fixture JSONL de 33 010 cas synthétiques, l'entraînement passe de
17,428 à 8,942 secondes en médiane, avec les mêmes poids et résultats
d'évaluation. L'index interactif mesuré passe de 13,35 à 4,38 Mo en conservant
les mêmes données. Ces mesures ne préjugent pas des performances sur toutes
les pièces ou sur un autre ordinateur.
Le guide donne aussi le protocole de comparaison avec les sources rc4
incluses. Pour reproduire les chiffres rc4→rc5, exécuter ses commandes depuis
la version rc5 conservée ; une exécution du code rc6 mesure la rc6. L'archive standard conserve toutes les fonctions
du projet et ses preuves courantes ; les anciens historiques de calcul
non référencés peuvent être ajoutés avec `--include-history` lors d'un nouvel
empaquetage.

La planification d'un million de cas reste une capacité configurable. Les
mesures sur des enregistrements synthétiques ne signifient pas qu'un million
de maillages a été calculé.

Les [sources du pilote automatique rc10, ses 32 cas et son modèle entraîné](verification/direct_quad_hex/corpus/README.md) permettent de reproduire l’apprentissage et de partager le modèle.

## Corpus et responsabilités des programmes

L'application permet de **planifier 250 000 configurations structurées et 750 000
configurations aléatoires** sans exécuter ce million de cas. Les calculs sont
déclenchés par lots bornés, conservés dans des segments et repris après
interruption. L'entraînement parcourt les données par mini-lots ; les familles
réservées à la validation et au test sont distinctes de l'entraînement.

Le panneau Corpus de la page Maillage et les commandes `atelier corpus`
pilotent les mêmes opérations Rust. Le [guide du corpus et des langages](docs/CORPUS_ET_LANGAGES.md)
donne les commandes et décrit précisément la répartition : **Python assure
l'interface, les fichiers et les exports ; Rust pilote les traitements
numériques**, en appelant Gmsh/OpenCASCADE natifs C/C++.

Le million de configurations est un objectif configurable, pas un corpus déjà
calculé ou une preuve de généralisation. Les lots réellement exécutés figurent
dans les rapports de validation. Les informations spatiales et topologiques
enrichissent la description du modèle ; elles ne transforment pas le réseau en
mailleur autonome plaçant directement chaque nœud.

## Vérifications exécutées sur la rc6

Les rapports historiques cités ci-dessous sont conservés dans l’archive locale de la rc6 d’origine. Le [dossier rc10](verification/rc10/README.md) contient les vérifications de la version publique.

La livraison du **19 novembre 2026** passe **195 tests Python et 139 tests
Rust**, sans échec ni test ignoré, sous Linux. Les huit exemples de livraison
ont été maillés à l'ordre 2 et leurs paquets d'export ont été produits.
Le rapport natif rc6 (`verification/native_release/summary.json`) consigne les
résultats réels, dont les contrôles de géométrie courbe des sphères et du
STL quadratique.

Le pilote direct (`verification/rc6/structured_corpus/summary.json`) a calculé
32 configurations, sur huit familles, et 96 stratégies acceptées. Le
pilote général (`verification/corpus/summary.json`) a traité 48 configurations,
dont 46 ont produit un maillage et 45 une stratégie admissible. Ces petits
pilotes sont distincts du million de cas planifiés, toujours non exécuté.

Les trois conseillers ont des résultats différents. Le guide historique
à huit entrées est activé **dans son domaine classique** après un gain de
validation d'environ 0,02046 sur son petit jeu de 10 cas d'entraînement et
six de validation. Ce résultat ne concerne pas les nouvelles grilles
directes. Le réseau de mémoire v3 et les conseillers de corpus restent
désactivés pour le classement, faute de gain validé ; la recherche et la
mémoire exacte revalidée fonctionnent indépendamment de cette activation.

La boucle API a été vérifiée avec de vrais maillages et des retours solveurs
fictifs identifiés. Aucun calcul physique dans les solveurs externes, ni
essai macOS/M3 ou Windows, n'est déduit de ces tests Linux.

## Vérifications exécutées sur la rc5

La rc5 du **4 novembre 2026** a été vérifiée sous Linux avec **150 tests Python
réussis sans test ignoré** et **110 tests Rust réussis sans échec ni test
ignoré**. Les 57 tests de l'ancien prototype numérique Python, retirés dans
la rc4 et conservés comme références, ne sont pas comptés comme de nouvelles
réussites. Les 16 tests de frontière et d'entrées/sorties qui les ont remplacés
restent exécutables.

Le plan cible contient un million de cas mais **zéro cas calculé**. Un pilote
séparé a réellement exécuté **48 cas**, dont 24 structurés et 24 aléatoires,
sur 24 familles : 46 cas ont produit un maillage et 45 ont au moins une
stratégie admissible. Les 96 stratégies comprennent 88 acceptations et huit
refus. La reprise a été effectuée dans une nouvelle session.

La recherche continue a également été exécutée sur six géométries distinctes,
dont une source STEP à deux volumes. Le réemploi revalidé et le refus d'un
seuil impossible sans maillage public ont été contrôlés.

Sur cette campagne rc5, les réseaux ont été actualisés, mais **leur classement
reste désactivé** : le pilote de corpus ne montre aucun gain de sélection sur la référence, et le
guide de mémoire ne démontre pas d'avantage de validation. La recherche des recettes et
la mémoire exacte revalidée fonctionnent indépendamment de cette activation.
Les tests sur macOS, Windows et dans les solveurs externes restent à effectuer.

Consulter les [résultats détaillés](docs/VALIDATION.md), le
rapport natif rc5 conservé (`verification/native_release/summary_rc5.json`) et le
rapport d'apprentissage continu rc5 (`verification/continuous_learning/summary_rc5.json`),
ainsi que le rapport du corpus rc5 (`verification/corpus/summary_rc5.json`).

## Installation et lancement

Extraire **tout le dossier source** dans un emplacement accessible en écriture.
Prévoir Python **3.11, 3.12 ou 3.13**. Si le noyau natif de votre plateforme
n'est pas fourni dans l'archive, installer aussi la chaîne **Rust avec Cargo**
depuis [rustup.rs](https://rustup.rs), puis ouvrir un nouveau terminal.

| Système | Lanceur |
| --- | --- |
| macOS | `Lancer.command` |
| Windows | `Lancer_windows.bat` |
| Linux | `Lancer_linux.sh` |

Le premier lancement crée un environnement `.venv`, installe l'interface et ses
dépendances, puis compile le noyau si nécessaire. **Internet est requis pour
ces téléchargements initiaux**. Le téléchargement de PySide6 peut être
conséquent. Le programme fonctionne localement après installation ; les calculs
ne demandent pas un navigateur ou un service hébergé.

Depuis un terminal placé dans le dossier du projet :

```sh
python3 scripts/install.py
python3 scripts/launch.py
```

Sur Windows, employer `py -3 scripts\install.py` et
`py -3 scripts\launch.py`. Si plusieurs versions de Python sont présentes,
choisir explicitement une version prise en charge, par exemple `py -3.12` ou
`python3.12`.

Si le lanceur macOS n'est pas exécutable :

```sh
chmod +x Lancer.command
./Lancer.command
```

L'installateur utilise un noyau déjà compilé s'il correspond à la plateforme.
À défaut, il exécute Cargo depuis les sources livrées. Il **n'installe pas Rust
automatiquement** et n'ajoute pas de paquets système. Une réinstallation
conserve l'environnement existant et peut être relancée après correction d'un
problème.

## Parcours en trois pages

### 1. Géométrie

Créer une pièce simple ou importer une géométrie prise en charge : STEP/STP,
IGES/IGS, BREP, STL ou une peau triangulée MSH/VTK classique/UNV selon le mode. Les maillages volumiques et les peaux quadrangulaires ne sont pas acceptés comme sources de reconstruction dans ces trois derniers formats. Définir l'unité d'entrée du modèle avant
les transformations. Les paramètres de création comprennent les dimensions,
les trous et, pour une plaque, les congés ou chanfreins du contour.

Les congés et chanfreins natifs de plaque ne sont pas un modeleur général de
congés 3D. Les volumes plus élaborés avec leurs détails sont importés depuis
un fichier CAO préparé. La visualisation, les informations de topologie et la
sélection servent à identifier les entités réellement présentes.

Les opérations de réparation sont optionnelles et utilisent une tolérance
explicite dans l'unité du modèle. Elles ne doivent pas confondre une ouverture
fonctionnelle et une face manquante. Un solide volumique demande une enveloppe
fermée et valide ; une surface ouverte peut rester exploitable en mode surface.

### 2. Maillage

Choisir le mode plan, surface ou volume, la taille globale et l'ordre des
éléments. La sélection d'entités permet de préciser la portée d'une opération
et d'affecter des tailles locales. Les correspondances entre entités et
paramètres doivent être conservées dans le projet et rendues visibles.

Pour un remaillage partiel strict, la conservation du maillage hors sélection
est une condition à vérifier, y compris à l'interface des régions. Si une
opération exige de modifier cette interface, le mode strict doit la refuser
ou la signaler explicitement ; un maillage complet régénéré n'est pas présenté
comme une conservation locale. Les garanties effectivement testées figurent
dans le rapport de validation de la livraison.

Dans cette première implémentation, la reprise locale rejoue en interne la
recette native précédente avant de modifier les entités sélectionnées. Les
cellules conservées sont comparées géométriquement, à la précision flottante
près. Ce coût de reconstruction est indiqué dans le rapport ; il ne s'agit
pas encore d'un noyau persistant gardant toute la session CAO en mémoire.
L'historique est borné à 32 reprises successives.

Le choix explicite de la forme conserve la famille demandée. Le parcours
classique produit des triangles et des tétraèdres ; les blocs compatibles
peuvent produire directement quadrilatères, hexaèdres, prismes ou pyramides.
La recombinaison est refusée. Une CAO incompatible avec les blocs directs
est signalée, sans substitution par des triangles ou des tétraèdres.
Les contrôles de qualité portent sur le fichier réellement calculé et pas
seulement sur son aperçu. Voir le [périmètre des blocs directs](docs/EVOLUTIONS_RC6.md).

### 3. Export

Choisir un profil de destination et une unité de sortie. La conversion d'unité
est une transformation explicite effectuée par le noyau ; modifier le texte
d'une unité ne suffit pas à convertir les coordonnées. Les noms et groupes
utiles au solveur doivent accompagner l'export.

| Destination | Passerelle prévue |
| --- | --- |
| Gmsh | MSH |
| Code_Aster / Salome-Meca | MED ; UNV selon le flux d'import |
| Abaqus / CalculiX | INP, avec formulation cohérente entre pièce plane, coque et solide |
| ParaView | VTU |
| Cast3M | UNV accompagné d'un script d'import `.dgibi` |
| ANSYS Mechanical/APDL | Commandes APDL construisant des éléments MESH200 |

Le détail des types pris en charge et de la conservation des groupes est dans
[docs/FORMATS.md](docs/FORMATS.md). Un export n'est pas un dossier de calcul
complet : matériaux, chargements, contacts et modèles physiques restent à
renseigner. MESH200 décrit la topologie sans degrés de liberté ; ANSYS demande
ensuite une affectation ou conversion vers les éléments physiques voulus.

Les profils sont des choix de conversion vérifiables, pas une promesse de
compatibilité avec chaque version de chaque logiciel. Un format échoué doit
être indiqué dans le manifeste plutôt que déclaré réussi.

## Unités et données

Les unités d'entrée, les tailles de maille, les rayons et les tolérances sont
liés. Vérifier l'unité de la source CAO, particulièrement pour les fichiers qui
ne la déclarent pas de façon exploitable. Le choix d'unité de sortie doit
produire des coordonnées et dimensions converties de manière cohérente.

Le dossier de travail par défaut est **`AtelierMailleur` dans le dossier
personnel**. Les projets doivent conserver leurs paramètres et leur source
importée pour permettre une réouverture reproductible. Les fichiers sources
ne sont pas écrasés par une réparation ou un export.

## Noyau Rust et développement

Les sources se trouvent dans `rust_core/`, avec `Cargo.toml` et `Cargo.lock`.
Pour compiler et tester le noyau :

```sh
cargo build --locked --release --manifest-path rust_core/Cargo.toml
cargo test --locked --manifest-path rust_core/Cargo.toml
```

Le pont cherche le binaire dans cet ordre :

1. Le chemin donné par `ATELIER_CORE`, s'il est défini.
2. `rust_core/target/release/mailleur-core` (`.exe` sous Windows).
3. `rust_core/bin/<plateforme>-<architecture>/mailleur-core` (`.exe` sous Windows).

Exemples de clés de plateforme : `darwin-arm64`, `linux-x86_64` et
`win32-amd64`. Un binaire Linux ne fonctionne pas sous macOS ou Windows. La
bibliothèque Gmsh installée doit elle aussi correspondre au système et à
l'architecture. La variable `GMSH_LIBRARY` permet d'en préciser le chemin si
la détection automatique ne convient pas.

Le paquet Python Gmsh fournit notamment cette bibliothèque native. Il n'est
pas utilisé comme moteur de calcul Python dans le parcours natif : Rust
appelle l'API C. Voir [ARCHITECTURE.md](docs/ARCHITECTURE.md).

Après activation de l'environnement Python :

```sh
# macOS / Linux
source .venv/bin/activate

# Windows, dans cmd.exe
.venv\Scripts\activate
```

Les commandes de développement comprennent :

```sh
python -m atelier desktop
python -m atelier doctor
python -m unittest discover -s tests -v
```

`python -m mailleur` redirige vers la CLI native de l'atelier. Le serveur local
utilise également le pont Rust. Les anciennes API de triangulation et
d'apprentissage du prototype sont retirées explicitement ; elles ne lancent
plus de calcul numérique Python en parallèle du noyau.

## Apprentissage sur la pièce courante

La page Maillage distingue **apprendre sur la pièce ouverte**,
**entraîner le guide historique sur son corpus synthétique** et le panneau de
**campagne de corpus par lots**. La première fonction
traite la vraie géométrie et ses réglages, y compris une source CAO importée.
Elle ne suppose pas que cette pièce ait déjà été rencontrée.

La recherche essaie un nombre borné de recettes : choix d'algorithme, taille
cible et variantes de raffinement. Chaque recette produit un vrai maillage,
évalué par le noyau Rust sur l'ensemble des éléments. Le résultat retenu doit
respecter la politique de qualité globale et les zones critiques que vous avez
marquées. Le nombre d'essais n'est pas un nombre de pièces nouvelles ni une
preuve d'optimalité globale.

Une mémoire locale conserve les expériences et leurs résultats. Une recette
retrouvée pour le même contexte est **recalculée et contrôlée** avant d'être
réutilisée. Le réseau est actualisé à partir de l'historique ; il peut contribuer à
l'ordre d'exploration des recettes après validation et ne remplace jamais leur validation.
Une amélioration sur la pièce courante n'établit pas, à elle seule, un gain sur
d'autres pièces.

Pour protéger une zone de contact, un raccord ou une région mécaniquement
sensible, sélectionner ses entités et ajouter une **zone critique**. Le type
« contrainte » est un marquage de priorité fourni par l'utilisateur : le
programme ne résout pas le problème mécanique et ne calcule pas spontanément
les efforts, contraintes ou erreurs de solution. Les contacts physiques et
les raccords doivent également être désignés explicitement.

Consulter le [guide d'apprentissage continu](docs/APPRENTISSAGE_CONTINU.md)
pour les critères d'acceptation, la mémoire, les tailles non uniformes et la
différence avec le conseiller historique. Deux exemples sont fournis :
[`apprentissage_plaque_zones.json`](examples/apprentissage_plaque_zones.json)
et [`apprentissage_volume_zone.json`](examples/apprentissage_volume_zone.json).

## Dépannage

- **Python incompatible** : sélectionner une version 3.11 à 3.13.
- **Cargo absent et aucun noyau fourni** : installer Rust depuis rustup.rs,
  ouvrir un nouveau terminal puis relancer l'installateur.
- **Compilation impossible** : conserver le message Cargo et vérifier la
  chaîne de compilation de la plateforme. L'installateur ne télécharge pas
  silencieusement une chaîne Rust.
- **Bibliothèque Gmsh absente** : relancer l'installation Python et le
  diagnostic ; préciser `GMSH_LIBRARY` si la bibliothèque est installée ailleurs.
- **Erreur native sous Linux** : vérifier les paquets de la distribution
  fournissant GLU, Xft et les dépendances Qt. La CI fournit un exemple Ubuntu.
- **Projet déplacé après installation** : relancer `scripts/install.py` dans
  son nouvel emplacement.
- **Aucun solide fermé détecté** : inspecter les surfaces et corriger la source
  CAO, ou demander une réparation adaptée, avant le maillage volumique.

## Portabilité et validation

Les sources et lanceurs visent macOS, Windows et Linux. La livraison source
n'est pas une application macOS signée/notariée ni un installeur Windows natif.
Une compilation correspondant au système est nécessaire si son binaire n'est
pas fourni dans l'archive.

La CI préparée teste Rust et Python sur les trois systèmes avec Python 3.11 et
3.12. Sa présence ne signifie pas qu'elle a déjà été exécutée. Les résultats
réellement obtenus, les cas de réparation, les unités, le remaillage partiel et
les imports solveur sont consignés séparément dans les rapports de livraison.

Consulter [LIMITES.md](docs/LIMITES.md), [VALIDATION.md](docs/VALIDATION.md),
[FORMATS.md](docs/FORMATS.md) et [THIRD_PARTY.md](docs/THIRD_PARTY.md).
