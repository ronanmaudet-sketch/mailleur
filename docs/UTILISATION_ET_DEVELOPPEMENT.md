# Mailleur : utiliser les imports étendus et contribuer au projet

Ce guide décrit les interfaces et l'intégration de la révision rc7 du **7 novembre 2026**. Il complète le [guide général](GUIDE_COMPLET_MAILLEUR.md), la [liste des formats](FORMATS.md), le [guide de publication](PUBLICATION.md) et les [vérifications étendues](VALIDATION_RC7.md). Les mesures Apple M3 du 4 novembre dans [MESURES_M3.md](MESURES_M3.md) concernent la révision mesurée à cette date : elles ne mesurent pas automatiquement les nouveaux lecteurs et exports.

Mailleur est une application locale de préparation géométrique et de maillage. Python fournit l'interface, les commandes, l'API HTTP et la lecture/écriture des formats. Rust assure les traitements numériques et pilote Gmsh/OpenCASCADE pour construire la géométrie et les maillages. Un petit réseau peut conseiller des recettes lorsque sa validation le permet ; le moteur classique reste responsable des nœuds, éléments et contrôles.

## 1. Ce que signifie la compatibilité 2020+

La cible du projet est l'échange avec les logiciels de CAO et de calcul utilisés depuis leurs versions 2020, sans priorité imposée à un seul éditeur. **Ce n'est pas une certification de tous les logiciels, formats et versions depuis 2020.** Il faut distinguer trois états :

| État | Signification |
| --- | --- |
| Lecteur ou écrivain intégré | Le code peut traiter le format ou un sous-ensemble documenté, avec des tests sur des fichiers déterminés |
| Conversion locale nécessaire | Un logiciel ou traducteur externe doit être installé et configuré ; reconnaître l'extension ne suffit pas |
| Validation externe restant à faire | Le fichier a été contrôlé par Mailleur ou relu par un lecteur, mais pas encore exécuté dans le logiciel cible |

Une version 2020 de SOLIDWORKS n'est pas supposée traduire complètement les fichiers natifs de toutes les versions plus récentes. Les extensions `.prt`, `.asm`, `.inp` ou `.dat` sont également ambiguës entre logiciels : le lecteur choisi et le contenu doivent correspondre, pas seulement le suffixe.

Un export de maillage n'est pas un modèle mécanique complet. Les matériaux, lois de comportement, épaisseurs, contacts, conditions aux limites, chargements et commandes de calcul restent à définir dans le solveur.

## 2. Installer et vérifier le logiciel

Prévoir Python 3.11, 3.12 ou 3.13. Si l'archive ne contient pas un binaire du noyau correspondant au système et à son architecture, installer aussi Rust/Cargo. Extraire le dossier complet dans un emplacement accessible en écriture, puis lancer depuis sa racine :

```sh
python scripts/install.py
python scripts/launch.py
```

Ici, `python` doit désigner une version prise en charge. Sur macOS, il peut être nécessaire d'employer explicitement `python3.12`. Les lanceurs `Lancer.command`, `Lancer_windows.bat` et `Lancer_linux.sh` suivent le même principe. Internet sert à installer les dépendances initiales ; les calculs fonctionnent ensuite localement.

Sur **Linux ARM64**, Gmsh 4.15.2 n'a pas de roue PyPI pour cette architecture.
L'installation habituelle échoue donc à cette dépendance. L'alternative
`environment-linux-arm64.yml` utilise conda-forge ; elle doit être lancée avec
Conda ou micromamba depuis la racine du projet, puis son environnement activé.
Compiler le noyau avec Cargo et utiliser directement son `python` pour les
commandes de Mailleur. Ne pas employer ensuite `scripts/install.py`, qui
créerait un autre environnement `.venv`. Les prérequis et résultats Ubuntu sont
décrits dans [VALIDATION_RC7.md](VALIDATION_RC7.md).

```sh
conda env create -f environment-linux-arm64.yml
conda activate mailleur-linux-arm64
cargo build --release --locked --manifest-path rust_core/Cargo.toml
python -m atelier doctor
python -m atelier
```

Pour les commandes suivantes, utiliser l'interpréteur de l'environnement créé :

| Système | Interpréteur depuis la racine du projet |
| --- | --- |
| macOS/Linux | `.venv/bin/python` |
| Windows | `.venv\Scripts\python.exe` |

Exemples macOS/Linux :

```sh
.venv/bin/python -m atelier doctor
.venv/bin/python -m atelier formats --json
```

La première commande vérifie l'installation ; la seconde expose le catalogue d'import/export de la version exécutée. Ce catalogue est plus fiable qu'une liste copiée depuis un ancien document. Pour une extension conditionnelle, lire `conditional`, `available`, `converter` et `reason`. Un exécutable trouvé ne garantit pas à lui seul que sa licence, sa classe COM ou la version du document sont utilisables.

Les petits réseaux actuels s'entraînent sur CPU, en double précision. Leur taille ne nécessite pas un GPU dédié. La mémoire et le temps de la génération des maillages doivent toutefois être mesurés sur les propres pièces de l'utilisateur ; une démonstration de quelques milliers d'éléments ne dimensionne pas un assemblage industriel.

La rc7 relit les flottants JSON exactement pour permettre une reprise des
poids identique entre processus. Sa provenance numérique est distincte des
versions précédentes : les anciennes observations restent conservées dans
leurs contextes. La migration d'intégrité d'un ancien corpus exige une
adoption explicite de ses octets actuels ; elle ne transforme pas une
campagne rc6 en campagne de génération rc7. Lire les étapes et limites dans
[VALIDATION_RC7.md](VALIDATION_RC7.md) avant de reprendre un ancien dossier.

## 3. Importer une pièce ou un maillage

### CAO et maillages intégrés

Le projet d'import contient les réglages géométriques et de maillage ; `--source` fournit le fichier à préparer. Par exemple, après adaptation des unités et du mode dans `examples/atelier_import.json` :

```sh
.venv/bin/python -m atelier mesh examples/atelier_import.json \
  --source /chemin/piece.step -o output/piece --formats msh,inp
```

Pour un fichier provenant d'un autre lecteur, remplacer la source tout en choisissant un mode compatible avec sa géométrie et ses unités. Le suffixe n'indique pas à lui seul s'il s'agit d'une peau ouverte, d'une surface fermée ou d'un domaine volumique maillable.

| Famille d'entrée | Extensions du registre | Traitement |
| --- | --- | --- |
| CAO d'échange | `.step`, `.stp`, `.iges`, `.igs`, `.brep` | Import de géométrie via Gmsh/OpenCASCADE |
| Peau STL | `.stl` | Parcours Gmsh de reconstruction surfacique |
| Maillage UNV | `.unv` | Lecture par Gmsh, puis préparation de peau dans Rust avant reconstruction |
| Surfaces polygonales | `.obj`, `.ply`, `.off` | Décodage du fichier, puis préparation numérique en Rust |
| Maillages généraux | `.msh`, `.vtk`, `.vtu`, `.med`, `.mesh`, `.meshb`, `.vol` | Lecture selon le format déclaré, puis reconstruction à partir de la peau |
| Maillages de solveurs | `.inp` pour le lecteur Abaqus ; `.bdf`, `.nas`, `.fem` pour le lecteur Nastran | Lecture du sous-ensemble de maillage reconnu, sans transfert automatique du modèle physique |
| Conteneur 3MF | `.3mf` | Lecture de la géométrie 3MF Core et des instances prises en charge |

L'import d'un maillage a pour objectif de **produire une géométrie de reconstruction à remailler**. Il ne conserve pas automatiquement les identifiants des anciens éléments ni leurs groupes. Les noms et champs repérés par le lecteur sont consignés dans les métadonnées ; ils ne deviennent pas des chargements, des matériaux ou des groupes du nouveau maillage. Garder l'original pour comparer et recréer les sélections physiques nécessaires.

Les éléments, ordres et connectivités acceptés pour reconstruire la peau sont documentés dans [FORMATS.md](FORMATS.md). Un lecteur de fichier peut décoder une cellule que le noyau refuse ensuite : le logiciel doit signaler ce refus plutôt que supprimer silencieusement la cellule. Les fichiers de solveurs avec `INCLUDE` externes doivent être rendus autonomes avant l'import.

Le préparateur Rust accepte les familles documentées de triangles, quadrangles, tétraèdres, hexaèdres, prismes et pyramides, avec plusieurs connectivités d'ordre supérieur. Pour ces dernières, il reconstruit une peau à partir des **sommets principaux** : c'est une approximation géométrique déclarée, pas un transfert éléments finis conservant exactement les nœuds intermédiaires et la courbure. Les quadrangles de peau sont triangulés. Les Jacobiennes courbes du maillage source ne sont donc pas certifiées par cette opération.

Les nouveaux tests de préparation couvrent des cellules linéaires tétraédriques, hexaédriques, prismatiques et pyramidales, ainsi que des cas `tetra10`, `triangle6` et `quad8`. Les autres variantes d'ordre supérieur reconnues par le contrat ne disposent pas chacune d'un essai individuel annoncé : ne pas transformer l'existence du mapping en validation de tous les maillages provenant de tous les solveurs.

En présence de cellules volumiques, les faces internes partagées sont retirées selon leur connectivité ; seules les faces de frontière servent à la reconstruction. Les anciennes interfaces internes, matériaux et ensembles ne sont pas recréés automatiquement. Le contrôle rejette notamment les indices invalides, les coordonnées non finies, les cellules primaires inversées ou non convexes et les peaux non conformes aux contrôles topologiques. Il ne réalise pas une recherche exhaustive de toutes les auto-intersections possibles.

### Particularités de 3MF Core

Le lecteur prend en compte les objets maillés, les composants internes au modèle, les éléments du `build`, leurs instances et leurs transformations. Il lit l'unité déclarée dans le modèle — millimètre par défaut en 3MF — et transmet au noyau la conversion vers l'unité d'entrée choisie dans le projet. Le noyau reste responsable de l'application numérique des transformations et du contrôle des coordonnées.

Le périmètre est géométrique : couleurs, textures, matériaux d'impression et réglages de fabrication ne deviennent pas des données de calcul mécanique. Les extensions obligatoires non gérées, les composants externes ou multifichiers, les objets de type autre que `model`, les cycles et les références absentes sont refusés. Le test entre instances repose sur leurs boîtes englobantes : des boîtes qui se touchent ou se chevauchent provoquent un refus conservateur, même si les formes réelles pourraient être séparées. Il ne s'agit ni d'un test exact d'intersection ni d'une union booléenne des objets.

Les limites courantes du lecteur incluent 200 Mio pour la source et le contenu décompressé, 4 096 entrées d'archive, 2 048 instances et deux millions de points/cellules. La profondeur des références d'instances est bornée. Ces plafonds sont des contrôles d'entrée ; ils ne garantissent pas qu'un fichier atteignant chaque plafond sera rapide ou tiendra dans la RAM disponible. L'API ou le projet portable peuvent imposer d'autres limites : prendre la plus restrictive du parcours utilisé.

### Fichiers natifs avec convertisseur

Les formats natifs SOLIDWORKS, CATIA, Inventor, NX/Creo et plusieurs autres conteneurs sont reconnus comme **entrées conditionnelles**. Sans convertisseur local, ils sont refusés avec un diagnostic et une proposition d'export STEP. La liste exacte est dans `atelier/import_registry.py` et dans le catalogue de capacités.

Le mécanisme de conversion reçoit uniquement une commande définie par la configuration locale de l'installation. Un projet reçu, un `.cadpack` ou un fichier CAO ne peut pas définir la commande à exécuter. La liste d'arguments est lancée sans shell. Le convertisseur doit créer un STEP, IGES, BREP, STL ou MSH, dans les limites et le délai configurés ; la géométrie est encore validée par le noyau.

La passerelle fournie pour SOLIDWORKS demande Windows, PowerShell 64 bits STA, une installation desktop et des droits d'utilisation appropriés. **Elle n'a pas été essayée avec un SOLIDWORKS réel sur le Mac de l'audit.** Les neuf scénarios de protocole avec doubles ont été exécutés sous PowerShell sur Mac ; ce test ne constitue pas une certification de traduction ni un essai COM Windows. Voir [IMPORTS_NATIFS.md](IMPORTS_NATIFS.md) pour la configuration `MAILLEUR_CAD_CONVERTERS`, les options, les erreurs, les assemblages supprimés/non résolus et les limites de version.

### Transporter un assemblage : `.cadpack`

Un assemblage natif peut dépendre de dizaines de fichiers. Copier seulement son fichier principal dans une session ne les transporte pas. Le conteneur `.cadpack` rassemble une arborescence cohérente et un manifeste :

```json
{"schema":"atelier.cadpack.v1","source":"assembly.sldasm"}
```

Préparer un dossier contenant ce fichier principal et toutes les références requises, puis créer le paquet :

```sh
.venv/bin/python -m atelier pack-cad --directory /chemin/assemblage \
  --source assembly.sldasm --output output/assemblage.cadpack
```

Les chemins du paquet sont relatifs. Les chemins absolus, remontées `..`, liens symboliques, archives chiffrées et paquets imbriqués sont refusés. Le manifeste ne répare pas les références internes de la CAO. Vérifier d'abord que l'assemblage se rouvre depuis le dossier préparé, par exemple après un Pack and Go SOLIDWORKS. Le convertisseur du fichier principal reste nécessaire sur la machine qui ouvre le `.cadpack`.

L'outil `pack-cad` sélectionne les fichiers dont les extensions CAO sont reconnues et signale le nombre d'autres fichiers omis. Il ne prétend pas embarquer des tables Excel, des textures ou toutes les ressources que le logiciel d'origine pourrait référencer. Il ajoute les empreintes des fichiers sélectionnés au manifeste. Vérifier qu'aucune dépendance nécessaire à l'export n'a été omise ; à défaut, exporter un STEP autonome depuis le logiciel d'origine.

Un STEP autonome exporté par l'auteur reste une autre manière de transmettre la géométrie. Le paquet natif ne transporte pas son logiciel de création, sa licence, ni les droits de redistribution de ses pièces.

## 4. Générer, contrôler et exporter

Dans l'interface Qt, la sélection de source utilise le registre d'import et la liste d'export utilise `FORMATS`. Les formats conditionnels peuvent apparaître dans la boîte de sélection ; leur utilisation dépend encore du convertisseur annoncé par le catalogue. Contrôler les unités d'entrée, le mode, la réparation éventuelle et les zones avant le calcul.

Le rapport de génération indique si le maillage est accepté. Vérifier le minimum de qualité, les éléments médiocres et chaque région protégée. La marge d'une zone utilise une sélection conservatrice ; une zone nommée « contact » ne définit pas une loi de contact. La géométrie seule ne révèle pas les champs de contraintes d'un chargement inconnu.

Pour exporter une même pièce dans plusieurs formats, les identifiants suivants s'emploient avec `--formats`, séparés par des virgules :

| Identifiant | Destination | Contenu et limites principales |
| --- | --- | --- |
| `msh` | Gmsh MSH | Maillage natif d'échange du projet |
| `inp` | Abaqus | Nœuds, éléments et groupes selon les adaptateurs pris en charge |
| `med`, `unv`, `vtu` | Formats d'échange/visualisation | Selon les types et groupes documentés |
| `cast3m` | UNV et script d'import `.dgibi` | Import de maillage ; aucun calcul mécanique complet n'est créé |
| `ansys` | `.dat` historique, MESH200 | Parcours de maillage existant ; distinct du nouvel export structurel |
| `nastran` | `.bdf` | Sous-ensemble structurel BDF, identifiants, ensembles et propriétés à compléter |
| `lsdyna` | `.key` | Éléments linéaires pris en charge, nœuds et ensembles ; matériaux/sections à définir |
| `ansys_structural` | `.apdl` | Types structurels explicitement associés aux éléments acceptés ; paramètres physiques à compléter |

Les exports nouveaux ne visent pas tous les éléments imaginables. BDF prend notamment en charge les triangles/quadrangles, tétraèdres et hexaèdres d'ordres documentés, et le prisme linéaire. Le KEY se limite aux éléments linéaires pris en charge. L'APDL structurel distingue le plan, les coques et les volumes ; par exemple, le mode plan exporté doit être interprété selon son option de contrainte plane, pas comme une déformation plane ou une coque 3D par défaut. Une combinaison non validée est refusée. [FORMATS.md](FORMATS.md) précise les cartes, types, ordres, groupes et limites pour chaque écrivain.

Exemple :

```sh
.venv/bin/python -m atelier mesh examples/atelier_plate.json \
  -o output/plaque --formats msh,inp,nastran,lsdyna,ansys_structural
```

L'export convertit les unités depuis les mètres internes vers l'unité demandée, puis vérifie les artefacts et produit un manifeste. Ne pas définir une seconde conversion d'unité contradictoire dans le solveur. Les noms de groupes et les identifiants d'ensembles doivent être consultés dans les rapports, surtout quand un format distingue mal les ensembles de nœuds et d'éléments.

**Une relecture par meshio ou un lecteur de sous-ensemble n'est pas une exécution dans le logiciel cible.** La campagne rc7 a réellement exécuté cinq cas Cast3M 2026.0 et huit cas CalculiX 2.23, avec comparaison à une solution mécanique analytique. Abaqus, ANSYS, LS-DYNA et Nastran restent à essayer dans leurs installations réelles. Les preuves et leur périmètre figurent dans [VALIDATION_RC7.md](VALIDATION_RC7.md).

## 5. Utiliser l'API locale

Lancer une instance locale, avec un seul travailleur pour commencer :

```sh
.venv/bin/python -m atelier serve --host 127.0.0.1 --port 8765 \
  --workspace output/api --workers 1 --timeout 300 --no-browser
```

Le catalogue est accessible par `GET /api/formats`. `GET /api/info` fournit aussi `import_formats`, objet de schéma `atelier.import_capabilities.v1`, avec une ligne par extension : `extension`, `kind`, `conditional`, `available`, et éventuellement `converter` et `reason`. Lire aussi `converter_configuration_error` : une erreur de configuration est différente d'un fichier CAO géométriquement invalide.

Le cycle général reste le suivant : lire les informations et le jeton de session, téléverser une source si nécessaire, créer un travail, suivre son état, puis récupérer rapport et export. Le fichier natif ou le `.cadpack` emprunte la même préparation que la CLI et Qt. Les limites du téléversement peuvent être inférieures aux limites internes du lecteur et l'encodage base64 augmente le volume transmis. Les erreurs de conversion restent des erreurs, pas des maillages réussis.

Les routes de travaux, sources, apprentissage, téléchargement et annulation sont détaillées dans [API_UTILISATION.md](API_UTILISATION.md). L'API HTTP n'est pas un serveur public multi-utilisateur prêt à héberger des CAO privées ; son modèle d'accès vise l'ordinateur local. Le jeton et les vérifications d'origine ne constituent pas une gestion des comptes, des droits et des espaces de travail séparés.

## 6. Architecture du parcours d'import

```text
CLI / Qt / API HTTP
        │
        ├─ import_registry.py : noms, lecteurs et extensions conditionnelles
        │
        └─ NativeSession : conservation de la source et espace de calcul
                  ├─ CAO compatible / STL → parcours Gmsh direct
                  ├─ UNV → lecteur Gmsh et préparation de peau Rust
                  └─ import_worker.py : autres décodages avec délai
                            │
                            └─ imports.py
                                 ├─ convertisseur local → STEP ou autre géométrie
                                 └─ lecteur maillage / 3MF → mesh_input.json
                                                          │
                                  DTO atelier.mesh_import.v1
                                                          │
                               Rust prepare_mesh_import
                                 contrôle, transformations, préparation de peau
                                                          │
                               MSH de préparation → parcours Gmsh
                                                          │
                            maillage → qualité → rapport et exports
```

| Élément | Rôle à préserver |
| --- | --- |
| `atelier/import_registry.py` | Catalogue léger utilisable par l'interface sans charger un traducteur CAO |
| `atelier/imports.py` | Validation des conteneurs, décodage des formats, choix du convertisseur local, sérialisation et provenance |
| `atelier/import_worker.py` | Exécution du décodage dans un processus distinct et écriture d'un diagnostic de préparation |
| `atelier/native.py` / `NativeSession` | Copie de la source, lancement des processus, délai, métadonnées, chaînage vers Rust et lecture des résultats |
| `atelier.mesh_import.v1` | Contrat JSON de points, cellules, objets, matrices et échelle ; aucun code exécutable reçu |
| `prepare_mesh_import` dans le noyau Rust | Validation numérique et géométrique du DTO, construction de la peau et du fichier de préparation |
| `atelier/formats.py` et `convert_worker.py` | Exports, conversions et vérifications propres au format de destination |

Le pont vers Rust utilise des requêtes JSON sur disque et un processus `mailleur-core --request FICHIER`. Cette architecture n'est pas le serveur permanent JSON-lines de l'ancienne synthèse v2.5. Les fichiers de session permettent de distinguer erreur de lecture, convertisseur absent, source préparée invalide, maillage refusé et export impossible.

`import.json` décrit la préparation ; `converter.log` conserve la sortie du convertisseur lorsqu'il est utilisé. Le DTO temporaire s'appelle `mesh_input.json` et peut être supprimé après production du MSH canonique pour éviter une copie JSON volumineuse supplémentaire. La source originale et la source canonique restent conservées. Les métadonnées de session indiquent l'empreinte de l'intermédiaire ; le remaillage vérifie la géométrie, les unités, le mode et l'intégrité de cette préparation avant de la réutiliser.

La lecture d'un format et les opérations de représentation — par exemple ranger les coefficients d'une matrice 3MF dans le contrat attendu — ne créent pas un second moteur numérique Python. L'application effective des transformations, la qualité et la construction du maillage appartiennent à Rust et aux bibliothèques natives qu'il appelle.

## 7. Entraîner et partager un guide

Le parcours `learn` essaie des recettes sur la pièce importée comme sur une primitive compatible. Une stratégie retrouvée dans la mémoire est toujours exécutée et contrôlée de nouveau. Le classement neuronal ne peut pas rendre admissible un élément qui viole les règles de qualité.

Les guides de corpus et de mémoire sont des petits réseaux, pas des archives de toutes les pièces. Les tailles mesurées dans la livraison initiale sont détaillées dans [TAILLES_ET_CAPACITES.md](TAILLES_ET_CAPACITES.md). Le nombre de configurations augmente surtout le corpus, les journaux et le coût de génération ; il ne multiplie pas mécaniquement le nombre de poids d'une architecture fixe.

Pour transmettre un guide compatible :

```sh
.venv/bin/python scripts/share_model.py --kind corpus \
  --model MON_CORPUS/models/current.json --output output/mon_guide.zip
```

Le paquet léger ne copie pas les CAO ni les journaux par défaut. Le guide de corpus s'installe dans l'espace de travail du destinataire selon le README du paquet ; celui-ci doit aussi installer le programme et ses dépendances. Un modèle valide mais désactivé reste désactivé.

Le guide interactif seul ne permet pas de reprendre une mémoire neuve : l'apprentissage cumulatif demande aussi les observations des groupes déjà appris. Transmettre une mémoire complète implique d'examiner ses sources et son historique. Une licence du code n'accorde pas automatiquement des droits sur les pièces d'entraînement d'autres auteurs.

## 8. Développer et optimiser avec des preuves

Commencer par un cas minimal partageable : fichier source, unité, mode, paramètres, configuration du convertisseur, version des bibliothèques, commande et résultat attendu. Pour un incident de natif, vérifier d'abord que la source et ses références s'ouvrent dans le logiciel d'origine. Un problème de traduction ne se corrige pas en assouplissant arbitrairement les seuils de qualité.

Pour ajouter un format, séparer les responsabilités : entrée dans le registre, décodage en DTO ou convertisseur explicitement configuré, validation numérique Rust, puis preuves sur les cellules et cas limites. Ne pas annoncer une famille de formats sur la seule réussite d'un fichier. Pour un export, documenter les types exacts, l'ordre des nœuds, les ensembles, les unités, les cartes physiques encore à définir et les refus attendus.

Les priorités d'optimisation concernent le poste effectivement coûteux :

1. **Mesurer par phase.** Distinguer lecture/décompression, copie de source, sérialisation DTO, conversion propriétaire, préparation Rust, Gmsh, contrôle de qualité, export et téléchargement. Comparer la médiane de plusieurs exécutions identiques en release ; séparer la première ouverture et les parcours réutilisés.
2. **Réduire les copies inutiles.** Mesurer le coût des tableaux Python, listes JSON et fichiers intermédiaires. Préférer une lecture par blocs pour les gros fichiers lorsque le format et les contrôles le permettent. Un fichier compact sur disque peut se dilater fortement pendant le décodage ; mesurer aussi le pic de mémoire.
3. **Réutiliser seulement ce qui reste valide.** Une clé de cache doit tenir compte du contenu de source, des dépendances d'assemblage, de l'unité, des transformations, de la version du lecteur/convertisseur et du contrat numérique. Le nom du fichier ou sa seule date ne suffisent pas. Modifier une recette ou une zone critique invalide les résultats qui en dépendent.
4. **Préserver les contrôles.** Ne pas supprimer les empreintes, ignorer une cellule, accepter un composant non résolu ou transférer aveuglément des anciens groupes pour gagner du temps. Une optimisation de copies ou d'allocations doit comparer les mêmes coordonnées, connectivités, unités et diagnostics.
5. **Limiter le parallélisme au besoin réel.** Les processus partagent RAM et disque. La passerelle SOLIDWORKS se sérialise et suppose une session sans documents ; multiplier les travailleurs ne la rend pas indépendante de ses contraintes de licence ou de session.

Les petits réseaux ne constituent pas la première cible si la traduction CAO ou la génération domine. Une modification de réseau exige des jeux réservés et un gain de sélection mesuré ; une accélération de son entraînement n'est pas une accélération automatique de tous les maillages.

### Vérifications avant une contribution

Après installation, depuis un environnement où `python` et Cargo désignent les bons outils :

```sh
cargo test --locked --manifest-path rust_core/Cargo.toml
cargo build --locked --release --manifest-path rust_core/Cargo.toml
python -m atelier doctor
python -m unittest discover -s tests -v
```

Pour les tests Qt sans affichage, définir `QT_QPA_PLATFORM=offscreen` selon le shell utilisé. Exécuter d'abord les tests ciblés, puis les suites et parcours réellement concernés par le changement. Les tests de protocoles simulés de la passerelle PowerShell sont distincts d'un essai SOLIDWORKS réel.

La matrice [GitHub Actions](../.github/workflows/tests.yml) prévoit Linux, macOS et Windows avec Python 3.11 et 3.12. Sa présence ne prouve pas que ces six combinaisons ont été exécutées. Rapporter séparément tests locaux, CI effectivement terminée, relectures de fichiers et essais dans les logiciels propriétaires. Les anciens rapports Linux et Apple M3 restent datés de leurs campagnes ; ne pas les renommer pour les présenter comme des essais rc6.

## 9. Distribuer et publier

Le projet peut être préparé localement pour GitHub sans créer de dépôt distant. Le titulaire a choisi la **GNU GPL version 3 ou toute version ultérieure** (`GPL-3.0-or-later`) pour le code propre au projet ; le texte officiel figure dans [LICENSE](../LICENSE). Aucune identité d'auteur n'a été inventée. Consulter [PUBLICATION.md](PUBLICATION.md) avant de créer une release et [THIRD_PARTY.md](THIRD_PARTY.md) pour les composants tiers.

Une contribution utile indique le problème, le comportement corrigé, les formats et cas touchés, les vérifications, les mesures et les limites restantes. Joindre des exemples dont le partage est autorisé. Conserver les informations nécessaires pour reproduire un refus est aussi utile que joindre un maillage réussi.
