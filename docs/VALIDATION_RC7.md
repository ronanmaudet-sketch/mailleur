# Vérifications étendues de Mailleur — rc7

Campagne du 7 novembre 2026, sur MacBook Pro Apple M3, 16 Go, macOS 27.2
(Darwin 27.2, arm64). Les fichiers de preuve sont dans
`verification/extended_20261107/`. Cette campagne complète les
[essais rc6](VALIDATION_RC6.md), dont l'archive et les résultats restent conservés.

## Portée et lecture du rapport

Un test réussi porte sur les données, versions et critères effectivement
exercés. Il ne prouve pas la compatibilité avec tous les fichiers d'un format,
tous les logiciels depuis 2020 ou toutes les lois de calcul. Un refus de
géométrie invalide attendu est séparé d'un maillage accepté, d'un refus qualité
et d'une erreur inattendue. Une relecture par un parseur n'est pas une
exécution du logiciel cible.

## Résultats finaux après correctifs

| Environnement ou parcours | Résultat effectivement obtenu |
| --- | --- |
| Mac M3, Python 3.12.16 | 200 tests Python, 132 tests Rust release ; aucun échec ou ignoré |
| Mac, Python 3.11.18 et 3.13.18 isolés | 200 tests Python chacun, doctor et dépendances cohérents |
| Ubuntu 24.04 ARM64, Python 3.12.16 | 200 tests Python, 132 tests Rust release ; aucun échec ou ignoré |
| Imports directs étendus | 48 cas et 432 contrôles réussis |
| Cast3M 2026.0 arm64 | 5 calculs réels, 193 contrôles réussis |
| CalculiX 2.23 arm64 | 8 calculs réels et comparaisons analytiques réussis |
| Matrice géométrique | 288 essais : 257 acceptés ; refus détaillés ci-dessous ; aucun crash ni timeout |
| Propriétés d'import | 41 reconstructions acceptées, 8 rejets invalides attendus |
| Passerelle SOLIDWORKS | 9 scénarios simulés réussis ; aucune conversion SOLIDWORKS réelle |

Les temps des suites finales sont 43,965 s pour Python et 6,02 s pour Rust sur
le Mac ; 28,225 s et 0,92 s dans la VM Linux. Des tâches s'exécutaient en
parallèle : ces chiffres ne constituent pas un benchmark comparatif Mac/Linux.
Le noyau Mac utilisé par les derniers essais porte le SHA256
`39fde2270d7282ebe460591308edebd6d7a978601e519fdddf441ee8d9d9cd85`.

Les suites de Python 3.11/3.13 sont des mises à jour de copies rc6 installées
auparavant, pas deux installations vierges rc7. Les sources, l'origine des
imports, les versions du code et des métadonnées ont été contrôlées. Voir
`installation/rc7_python311/`, `installation/rc7_python313/`, `final_mac/` et
`linux/final_results/` dans le dossier de campagne.

## Géométries, unités et refus réellement observés

La matrice explore 24 familles, trois graines, deux ordres et deux unités,
soit **288 essais réels** : 257 maillages acceptés, 12 refus de la politique
de qualité, trois refus pour éléments inversés/dégénérés, quatre refus
structurés de l'optimiseur Gmsh et 12 géométries invalides intentionnelles
refusées. **Ces refus ne sont pas comptés comme des maillages réussis.** Aucun
crash, timeout ou échec des invariants contrôlés ne reste dans la campagne finale.

Sur 131 paires m/mm comparables, géométrie et tailles physiques concordent.
127 paires donnent la même décision de qualité, quatre une décision différente.
Les maillages ne sont pas identiques entre unités : les arrondis et les
décisions géométriques Gmsh peuvent modifier la triangulation. Le résultat
doit donc toujours être contrôlé après génération ; aucune invariance de
connectivité ni de décision de qualité entre représentations d'unités n'est
garantie. Huit répétitions d'une même requête sont identiques dans
l'environnement mesuré.

Les 49 contrôles de préparation comportent 41 reconstructions acceptées et
huit rejets explicites de données invalides. Les journaux gardent les
tentatives antérieures, dont les quatre arrêts Gmsh et les six incohérences
de boîtes courbes avant leur correction.

Preuves : `verification/extended_20261107/core/run-20261107T190216151961/summary.json`.

## Installations à partir de l'archive rc6

Deux extractions indépendantes de l'archive rc6 ont été installées dans de
nouveaux environnements Python **3.11.18** et **3.13.18**. Chacune a recompilé
le noyau à partir des sources et téléchargé ses dépendances ; aucun paquet ni
noyau du dossier de développement n'a été utilisé à leur place.

Pour chacune : **195 tests Python et 121 tests Rust réussis, aucun ignoré**,
diagnostic et dépendances cohérents, trois exemples en ligne de commande,
upload HTTP réel puis téléchargement BDF/ZIP, et trois pages Qt avec import
3MF, calcul et export BDF. L'incident initial de téléchargement sans réseau
est conservé séparément ; il n'est pas compté comme un test réussi.

Ces résultats établissent l'installation de l'archive rc6 portant le SHA256
`4b87e592967a44915750beb8b99c2b54d5bcfce89667ef968666239cb3bb1864`.
Ils ne remplacent pas la revalidation des correctifs rc7.

Preuves : `verification/extended_20261107/installation/INSTALLATION_NEUVE.md`.
Script reproductible : `scripts/validate_clean_install.py`.

## Vrais calculs Cast3M

Cast3M **2026.0**, binaire arm64 natif, a réellement exécuté les fichiers
Gibiane et UNV exportés. **5 cas réussis, 193 contrôles** : TRI3/TRI6 en plan,
TET4/TE10 en volume, unités millimètres et cas volume quadratique en mètres.
Les nombres de nœuds/éléments, groupes de domaine et de frontières, groupes
nodaux et dimensions ont été comparés dans Cast3M.

Les calculs de traction emploient E = 210 GPa et ν = 0,3, avec contraction
latérale libre. Déplacements, contraintes et équilibre des réactions sont
comparés à la solution analytique. Le chargement résultant de 10 N donne des
réactions opposées de même amplitude. Les exécutions durent environ
**0,756 à 1,016 s** chacune ; ces petites éprouvettes ne dimensionnent pas un
calcul industriel.

Un avertissement Fortran `IEEE_INVALID_FLAG` est présent aussi sur une sonde
minimale Cast3M. Il est conservé dans les journaux ; codes de retour, niveaux
d'erreur et comparaisons numériques passent. Les matériaux, chargements et
commandes de résolution sont ajoutés par le banc de vérification. L'export
standard de Mailleur reste un maillage accompagné de son script d'import.

Preuves finales : [rapport Cast3M](cast3m/preuves/RAPPORT_CAST3M_RC7.md),
[résumé](cast3m/preuves/summary.json) et [sélection des scripts/journaux](cast3m/preuves/README.md).
Cette plage rectifie celle d’un essai antérieur restée dans le guide ;
voir l’[erratum de publication](../ERRATUM_PUBLICATION.md). Les cinq durées
proviennent du dernier rejeu du 7 novembre, sans nouveau calcul le 10 novembre.
Script de reproduction : `scripts/validate_cast3m.py`.

## Vrais calculs CalculiX

CalculiX **2.23**, installé dans un dossier de vérification isolé via
conda-forge, a exécuté **8 cas** : CPS3, CPS6, C3D4, C3D10, chacun en mètres
et millimètres. Le banc ajoute au fichier INP exporté un matériau élastique et
des conditions de traction homogène ; les faces latérales restent libres.

Pour εx = 0,001, la contrainte attendue vaut 210 MPa. Le déplacement de chaque
nœud, les contraintes aux points d'intégration, la réaction de la face droite
et l'équilibre global sont comparés à la solution analytique. Les plus grands
écarts relatifs observés sont environ **5,0 × 10^-8** pour les déplacements,
**3,0 × 10^-14** pour les contraintes et **2,6 × 10^-8** pour la réaction ; la
précision d'impression du fichier `.dat` limite notamment les deux premiers
contrôles de déplacements/réactions. Les résolutions durent **0,014 à 0,121 s**
sur ces petites éprouvettes (110 triangles ou 658 tétraèdres).

Le lecteur meshio 5.3.5 ne relit pas CPS6 ; cela n'a pas été assimilé à un
échec de CalculiX. Le banc inspecte les enregistrements de l'INP puis utilise
le véritable solveur et ses résultats. **Un calcul CalculiX n'est pas un
calcul exécuté dans Abaqus**, même si ces exports partagent une syntaxe INP.

Preuves finales : `verification/extended_20261107/calculix_final/summary.json`.
Les premières tentatives restent dans `calculix/` et identifient aussi les
défauts du premier banc de test. `calculix_corrected/` garde les premières
résolutions corrigées, avant le dernier réglage des bornes CAO.
Script : `scripts/validate_calculix.py`.
Référence : [manuel CalculiX 2.23](https://www.dhondt.de/ccx_2.23.pdf).

## Linux ARM64 : installation alternative réellement essayée

Une machine Ubuntu **24.04 ARM64**, noyau 6.8, a été démarrée localement par
Lima 2.3.0/Virtualization.framework, avec deux processeurs, 4 Gio de RAM et
sans partage des dossiers du Mac. Le téléchargement de l'image Ubuntu et de
Lima a été vérifié par SHA256. Ce test Linux est distinct des mesures natives
du Mac et de la matrice GitHub Actions.

Le parcours habituel `pip install -e .` a réellement échoué : PyPI ne fournit
pas de distribution Gmsh 4.15.2 pour Linux aarch64. La compilation Rust seule
a réussi. L'alternative conda-forge a ensuite fourni Gmsh et `python-gmsh`
4.15.2 ; l'installation éditable du projet, sans suppression de dépendances,
a réussi avec PySide6-Essentials 6.11.3 cohérent avec Qt de l'environnement.

Sur cette extraction rc6 : **195 tests Python réussis en 17,967 s**,
**121 tests Rust release réussis en 0,44 s**, aucun test ignoré. Doctor et
`pip check` passent. Python 3.12.16, Rust 1.99.1, NumPy 2.5.4, h5py 3.16.0,
meshio 5.3.5. Ces temps sont mesurés dans la VM et ne sont pas un comparatif
de rapidité Mac/Linux à ressources égales.

Le fichier `environment-linux-arm64.yml` décrit cette alternative. Rust/Cargo
et les bibliothèques système restent des prérequis. Il ne faut pas présenter
le lanceur `.venv` habituel comme une installation automatisée Linux ARM64
validée : ce parcours particulier demande l'environnement conda actif.
Preuves : `verification/extended_20261107/linux/`.

La rc7 finale a ensuite passé 200 tests Python et 132 tests Rust, avec versions
du module et de ses métadonnées toutes deux vérifiées. Un premier remplacement
de sources dans l'ancienne copie laissait des caches et métadonnées générés
rc6 ; ces artefacts de la seule copie de test ont été retirés, puis les
contrôles relancés. Les journaux intermédiaires ne sont pas présentés comme
la preuve finale.

## Capacité : un gros maillage et un entraînement en flux

Une boîte de **20 × 16 × 8 mm**, taille cible 0,42 mm, un seul thread et
budget 300 000 éléments a réellement produit **166 247 tétraèdres et
31 321 nœuds**. Qualité minimale 0,269276 pour un seuil demandé de 0,03 :
maillage accepté. Le dernier essai mesure **2,577 s** et un pic natif de
**372 506 624 octets, soit 355,25 Mio**.

Ce pic concerne le processus Rust et ses bibliothèques natives, mesuré par un
parent neuf via `RUSAGE_CHILDREN` (unité macOS : octets). Il n'inclut pas tout
l'éditeur, l'interface Qt, les historiques ou la VM. Un seul solide simple ne
fixe pas un plafond de mémoire pour un assemblage ou un maillage courbe. Ces
mesures de campagne, réalisées avec d'autres tâches actives, ne constituent
pas un comparatif de versions à ressources identiques.

Le parcours d'entraînement a aussi traité **100 000 enregistrements
synthétiques**, 200 000 résultats de stratégies et 133 726 670 octets JSONL,
sur deux époques par mini-lots de 128. **Aucun des 100 000 enregistrements
n'est présenté comme un maillage CAO réellement calculé.**

| Exécution de la fixture synthétique | Temps | Pic du processus Rust |
| --- | ---: | ---: |
| Pause après sept mini-lots | 4,551 s | 3 964 928 octets |
| Reprise jusqu'à la fin | 22,231 s | 4 751 360 octets |
| Entraînement continu de référence | 22,281 s | 4 096 000 octets |

Les poids, empreintes, métriques, validation et décision d'activation obtenus
après reprise sont maintenant strictement identiques à ceux de l'exécution
continue. Avant correction, quelques poids différaient de deux ULP lors de
la lecture JSON ; une tolérance de 10^-12 des anciens tests masquait cet écart.
Les comparaisons de poids exigent désormais l'identité des bits.

Cette fixture artificielle contient un signal de récompense apprenable et
active son petit modèle pour vérifier ce chemin positif. Elle ne démontre
aucun gain sur des géométries réelles, et son modèle n'est pas installé dans
l'espace de travail courant de l'utilisateur. Le pilote CAO historique reste
séparé. Le gros JSONL synthétique temporaire a été supprimé après mesure et
empreinte ; le script, les petites sorties et les rapports permettent de le
recréer.

Preuves :
`verification/extended_20261107/core/run-20261107T190216151961/summary.json`
pour le gros maillage final, et `core/run-20261107T185138766837/summary.json`
dans le même dossier de campagne pour l'entraînement synthétique.
Script : `scripts/validate_extended_core.py`.

## Correctifs issus des vérifications

### Champs numériques INP

Le format Python `.17g` pouvait produire des nombres dépassant 20 caractères,
notamment avec un exposant ou un signe négatif. CalculiX refuse certains de
ces champs ; d'anciens lecteurs Abaqus ont aussi cette limite. L'écrivain INP
borne désormais les champs, conserve de 13 à 17 chiffres significatifs selon
le nombre et normalise l'exposant. Un test contrôle les enregistrements émis
avec valeurs négatives et exposants extrêmes, ainsi que leur précision. Les
huit calculs réels vérifient ensuite unités, lecture et résultat physique.

### Optimiseur quadratique, IGES et dimensions courbes

Quatre cas quadratiques provoquaient une exception C++ Gmsh non interceptable
dans une région parallèle et l'arrêt du processus natif. L'optimisation utilise
maintenant le mode Gmsh sans exception, collecte le journal, restaure son état
et renvoie un refus structuré en cas d'erreur. Aucun seuil qualité n'a été
abaissé et aucun ordre quadratique n'a été remplacé silencieusement par du
linéaire. Une géométrie valide peut encore être refusée par l'optimiseur ou
les critères de qualité ; ce refus reste visible et interdit l'export.

Le défaut d'initialisation des unités IGES est corrigé par une première lecture
dans un modèle transitoire, jeté avant la lecture avec l'unité cible. L'option
globale est restaurée même si l'initialisation échoue. Les **48 cas d'import**
vérifient ordres 1/2, origine et dimensions, conservation de l'original,
STEP/STP, BREP, IGES/IGS, MESHb, NAS et FEM ; les IGES sont réellement écrits
avec en-têtes en mètres ou millimètres. Le rapport détaillé compte 432 contrôles.

Enfin, une sonde indépendante a montré que Gmsh 4.15.2/OpenCASCADE conserve
des boîtes d'arêtes dégénérées d'une sphère dans l'ancienne échelle après une
dilatation. **Les vrais nœuds maillés étaient déjà correctement en SI** ; les
dimensions de diagnostic pouvaient être incohérentes et affecter leurs
utilisations ultérieures. Le réglage officiel `Geometry.OCCBoundsUseStl=1`
est désormais activé avant toute construction/import, aussi pour la reprise.

Les rapports exposent `bounds_source`/`bbox_source`, l'approximation et les
paramètres de déflexion. Ces bornes sont issues d'une tessellation : **elles
ne constituent pas des extrema CAO exacts**, et peuvent sous-estimer un
extremum courbe. Les régressions inspectent réellement des sphères surface et
volume, en m/mm, les points de preview et des exports MED en unités opposées.
Les tolérances des comparaisons de boîtes sont liées à la déflexion annoncée ;
les coordonnées et les tailles de maillage ont leurs propres contrôles.
Ce réglage peut changer les maillages produits ; il fait partie de la nouvelle
provenance numérique rc7.

Preuves : `verification/extended_20261107/direct_imports/RAPPORT.md`,
`core/occ_scaled_sphere_probe.json` dans le même dossier de campagne, et les
trois tests de `tests/test_extended_core.py`. Référence des options :
[manuel Gmsh](https://gmsh.info/doc/texinfo/gmsh.html#Geometry-options).

### Ancien corpus : adoption explicite de la référence d'intégrité

Les anciennes campagnes ne possèdent pas les empreintes des segments déjà
fermés. Une empreinte calculée aujourd'hui ne permet pas de prouver que ces
octets sont restés identiques depuis leur création. La rc7 refuse donc leur
reprise silencieuse. Conserver une copie du dossier du corpus, examiner ses
données et journaux, puis décider explicitement d'adopter les octets actuels :

```sh
.venv/bin/python -m atelier corpus status --directory /chemin/corpus \
  --adopt-legacy-integrity
```

Cette opération conserve une marque permanente
`historical_integrity_proven: false` et `adopted_legacy_integrity: true`.
Les nouveaux plans sont vérifiés dès leur création. Le registre binaire
contient 56 octets par segment fermé ; le point de reprise reste de taille
bornée. Chaque ouverture/status relit les octets durables pour vérifier les
empreintes, sans charger les cas JSON en mémoire ni rehacher tout l'historique
après chaque cas dans un lot. Le coût d'ouverture augmente donc avec le
volume de données déjà conservé. Ne pas réutiliser le seul temps de reprise
rc5 comme mesure de cette vérification renforcée.

Après adoption, un corpus rc6 reste lisible et entraînable, avec sa provenance
historique conservée. La génération de nouveaux cas rc7 dans ce même dossier
est refusée : créer un **nouveau dossier de corpus** pour une campagne rc7.
L'adoption d'une référence d'intégrité ne réécrit pas la version du moteur
ayant calculé les observations antérieures.

Les 27 tests Rust ciblés couvrent les segments premier/milieu/dernier, les
registres altérés ou tronqués, les interruptions avant publication du
checkpoint, la migration explicite et la vérification avant entraînement.
Un checkpoint manquant ou un faux manifeste externe ne permet pas de
réancrer tacitement les données. Une revue indépendante a identifié ces deux
contournements supplémentaires ; ils ont été corrigés et testés.

Sur 96 cas réels, 14 segments et 1 329 346 octets, sept ouvertures donnent
une médiane de **6,31 ms** en rc7 pour tous les octets et 13 segments clos,
contre **3,67 ms** en rc6 qui ne contrôlait que 73 221 octets actifs. Le
registre occupe 728 octets et aucun cas JSON déjà commis n'est redécodé.
Cette mesure comprend le démarrage du processus et ne prédit pas le temps
d'ouverture d'un million de cas.

Preuves : `installation/rc7_corpus_revalidation/` dans le dossier de campagne.

## Vérifications externes qui restent non exécutées

L'utilisateur dispose uniquement de ce Mac. Aucune conversion réelle par
SOLIDWORKS, CATIA, Inventor, Creo, NX ou leurs SDK n'a été exécutée. Les
passerelles conditionnelles et leurs tests de protocole ne remplacent pas
ces installations et leurs licences. Les fichiers d'assemblage conservent
leurs dépendances dans CADPACK ; cela ne fournit pas un traducteur propriétaire.

Aucun calcul dans **Abaqus, ANSYS, MSC/Simcenter Nastran ou LS-DYNA** n'est
revendiqué. Les contrôles de connectivité, de syntaxe et de groupes de leurs
exports restent utiles, mais ces logiciels doivent encore lire les fichiers
et résoudre les éprouvettes sur une installation accessible.

Windows, macOS Intel et toutes les versions annuelles 2020–2026 de ces
logiciels ne sont pas certifiés par cette campagne. Le workflow GitHub Actions
est fourni ; il ne doit être présenté comme exécuté qu'après une véritable
exécution distante. Le projet n'a pas été publié automatiquement.
