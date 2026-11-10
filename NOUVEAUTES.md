# Évolutions de la version 1.0.0rc7 — 7 novembre 2026

- Champs flottants INP limités à 20 caractères tout en conservant 13 à 17
  chiffres significatifs : lecture compatible des petites coordonnées.
- Initialisation de l'import IGES dans Gmsh/OpenCASCADE avant le choix de
  l'unité cible ; contrôle d'en-têtes IGES en mètres et millimètres.
- Les erreurs de l'optimiseur quadratique Gmsh deviennent des refus structurés
  au lieu d'exceptions C++ non interceptées ; l'ordre et la qualité restent contrôlés.
- Lecture JSON exacte des flottants pour la reprise de l'entraînement ;
  provenance numérique révisée pour conserver la distinction avec les contextes antérieurs.
- Journal d'intégrité de tous les segments clos du corpus, avec adoption
  explicite des anciennes campagnes dépourvues de cette référence historique.
- Métadonnées GPL en expression SPDX et alternative d'installation Linux ARM64.
- Campagne élargie : installations neuves, géométries variées, gros maillage,
  reprise/altération de corpus, vrais calculs Cast3M et CalculiX, Ubuntu ARM64.

Les résultats finaux et limites sont dans
[VALIDATION_RC7.md](docs/VALIDATION_RC7.md). Les preuves rc6 sont conservées
séparément et ne sont pas renommées en réussites rc7.

---

# Évolutions de la version 1.0.0rc6 — 7 novembre 2026

- Imports intégrés étendus : OBJ, PLY, OFF, 3MF Core, VTU, INP, MED, BDF,
  Medit et Netgen, avec relecture MSH/VTK/UNV et reconstruction des peaux.
- Préparation native Rust : faces internes, triangulation, unités et
  transformations ; conservation des originaux et signalement des approximations.
- Passerelles locales configurables pour les formats propriétaires ; script
  SOLIDWORKS Windows COM vers STEP et conteneur d'assemblage `.cadpack`.
- Nouveaux exports Nastran BDF, LS-DYNA KEY et ANSYS structural APDL,
  sans réduction silencieuse d'ordre et avec limites de types documentées.
- Catalogue partagé CLI, API et Qt ; `atelier formats`, `/api/formats` et
  `atelier pack-cad` ; exemples OBJ et 3MF prêts à utiliser.
- Annulation conservée entre étapes, délai global et nettoyage des processus.
- Licence choisie : GPL-3.0-or-later ; guides publics PDF/Word et fichiers de contribution GitHub.

Validation locale : 195 tests Python et 121 tests Rust réussis sans test ignoré,
plus neuf scénarios simulés de passerelle PowerShell. Aucun solveur commercial
ni SOLIDWORKS réel n'a été exécuté. Voir [VALIDATION_RC6.md](docs/VALIDATION_RC6.md).
La cible 2020+ n'est pas une certification universelle.

## Historique : version 1.0.0rc5

Cette version cible les coûts répétés du parcours rc4, en conservant la
séparation entre l'interface Python et les traitements numériques natifs.

- Entraînement : réutilisation des espaces de travail, lecture tamponnée et
  réduction des évaluations répétées d'un même réseau.
- Corpus : reprise à partir d'un curseur durable et contrôle du préfixe déjà
  enregistré, avec migration des anciens points de reprise.
- Qualité : réutilisation de la connectivité native et indexation des zones
  sans marge ; les zones avec marge gardent leur sélection conservatrice.
- Vue : caches de géométrie d'affichage, de projection et de sélection,
  invalidés lorsque la caméra ou les données concernées changent.

Les effets doivent être lus par scénario dans le
[guide des optimisations](docs/OPTIMISATIONS_RC5.md). Un gain de temps sur une
fixture ne prouve ni un gain général sur toutes les pièces, ni une amélioration
de la précision mécanique ou de la généralisation du réseau.

Les mesures comparatives déjà consignées donnent notamment :

| Scénario mesuré | rc4 | rc5 |
| --- | ---: | ---: |
| Entraînement JSONL, 33 010 cas synthétiques, trois passages | 17,428 s | 8,942 s |
| Reprise d'un segment validé de 1 000 lignes | 15,524 ms | 2,753 ms |
| Index interactif de 5 000 observations, mêmes données décodées | 13,35 Mo | 4,38 Mo |
| Validation d'une boîte de 45 183 tétraèdres et douze zones | 282,35 ms | 115,73 ms |

Ces temps sont des médianes sur les fixtures détaillées dans le guide. Ils
ne décrivent pas le temps total de toute campagne de maillage. La vue est
nettement plus rapide lorsqu'elle réutilise son cache, mais son premier
affichage de la fixture de 20 000 triangles est ici plus lent : 269,6 ms
contre 244,3 ms. Aucun gain global de mémoire du processus n'est annoncé.

## Vérifications exécutées sur la rc5

La livraison du **4 novembre 2026** passe **150 tests Python** et **110 tests
Rust**, sans échec ni test ignoré. Le pilote du corpus a de nouveau traité
48 configurations et consigné 96 résultats de stratégies, avec la reprise
entre deux sessions. La recherche continue a été exécutée sur six géométries,
avec réemploi revalidé et refus d'un seuil de qualité impossible.

Les 57 tests de l'ancien moteur Python retirés dans la rc4 restent des
références textuelles ; ils ne sont pas ajoutés à ces réussites. Les critères
d'activation des réseaux restent inchangés : aucun gain de sélection n'est
démontré par ce petit pilote et les classements neuronaux restent désactivés.
Les validations macOS, Windows et dans les solveurs externes n'ont pas été
exécutées.

---

# Évolutions de la version 1.0.0rc4

La rc4 sépare le corpus de grande taille de la mémoire interactive d'une pièce.
Elle renforce aussi la frontière entre l'interface Python et le calcul natif.

| Sujet | rc3 | rc4 |
| --- | --- | --- |
| Taille de campagne | Mémoire interactive bornée, sans générateur massif | Plan déterministe configurable de 250 000 cas structurés et 750 000 cas aléatoires |
| Déclenchement | Recherche bornée sur la pièce ouverte | Parcours interactif conservé et campagnes calculées explicitement par petits lots |
| Stockage du corpus | Historique interactif indexé dans un fichier | Segments JSONL et progression durable, sans un fichier par cas planifié |
| Entraînement du corpus | Petit historique rejoué en mémoire | Lecture séquentielle et mini-lots avec points de reprise |
| Évaluation | Validation du guide, sans test industriel indépendant | Familles séparées entre entraînement, validation et test ; résultats de sélection des stratégies distincts des erreurs de prédiction |
| Représentation | 16 descripteurs principalement globaux | Description enrichie de 128 entrées, avec empreintes spatiales et informations topologiques |
| Anciennes API numériques Python | Ancien prototype encore présent dans les sources | Adaptateurs natifs ou erreurs de dépréciation explicites, sans second moteur Python |

La préparation d'un plan ne calcule pas le million de configurations. Les
échecs attendus, les budgets et les cas terminés sont comptés séparément.
L'entraînement n'autorise pas un résultat qui échoue aux critères de qualité.
Aucun gain du réseau ni aucune validation sur un million de cas n'est déduit
du nombre affiché dans le plan.

La [répartition détaillée Python / Rust et le guide du corpus](docs/CORPUS_ET_LANGAGES.md)
décrivent les commandes et les limites. Les anciens tests du prototype Python
ne constituent plus un contrat de calcul ; les tests des adaptateurs, des
opérations natives et du corpus servent à vérifier le parcours livré. Les
compteurs réellement exécutés figurent dans [le rapport de validation](docs/VALIDATION.md).

## Vérifications exécutées sur la rc4

La validation Linux a exécuté **140 tests Python et 93 tests Rust**, sans
échec ni test ignoré. Les 57 tests retirés du prototype Python sont conservés
comme références textuelles ; 16 tests de frontière et d'entrées/sorties
vérifient leurs nouveaux contrats. Cette évolution de périmètre explique que
les totaux ne soient pas directement comparables à ceux de la rc3.

Le plan d'un million de cas occupe trois fichiers de métadonnées, soit
5 058 octets, avec zéro évaluation du moteur. Le pilote séparé a calculé
48 cas en deux lots, avec reprise dans une nouvelle session : 46 ont produit
un maillage et 45 possèdent au moins une stratégie admissible. Les 96
stratégies ont donné 88 acceptations et huit refus ; les 140 appels natifs
incluent les 48 inspections de référence.

L'entraînement de corpus a parcouru ces données en deux passages et mini-lots
de 16 : 32 cas d'entraînement, huit de validation et huit de test, répartis par
familles. Le modèle 128→32→1 comporte 4 161 paramètres. Son gain de sélection
de validation vaut zéro : **le classement du réseau reste désactivé**.
Le parcours continu 128→12→1 a conservé son historique et le réemploi revalidé
sur six géométries ; aucun gain de généralisation n'est annoncé.

Ces chiffres sont détaillés dans le [rapport du corpus (archive complète)](docs/provenance/PREUVES_EXTERNES.md)
et le [rapport natif](verification/native_release/summary.json). Les tests de
streaming sur des enregistrements synthétiques ne sont pas ajoutés aux
48 cas réellement maillés ou essayés du pilote.

---

# Évolutions de la version 1.0.0rc3

La rc3 étend la reconstruction rc2 avec un parcours d'apprentissage sur la
pièce courante. Les fonctions historiques restent décrites plus bas ; aucun
résultat n'est comparé au projet Rust original, qui n'a pas été récupéré.

| Sujet | rc2 | rc3 |
| --- | --- | --- |
| Nouvelle géométrie | Génération classique ; apprentissage sur corpus synthétique séparé | Recherche bornée de recettes sur la pièce courante, y compris une source importée exploitable |
| Mémoire | Poids et rapports sauvegardés, sans réutilisation cumulative des expériences | Historique persistant des essais et recettes ; réemploi exact après recalcul et vérification |
| Réseau | Réinitialisé à chaque entraînement du corpus | Modèle de mémoire actualisé avec replay de l'historique pour classer les recettes |
| Qualité | Minimum et moyenne globaux | Critères globaux, nombre et sévérité des éléments médiocres, contrôle par zones critiques |
| Régions sensibles | Tailles locales et correspondances | Marquage contact/interface/contrainte/autre avec seuil et marge |
| Acceptation | Contrôles géométriques natifs | Contrôles natifs et exigences explicites de qualité ; échec annoncé si aucun essai admissible |

Le réseau de mémoire comporte 217 paramètres (16 entrées, 12 neurones cachés,
une sortie), séparément du guide historique de 147 paramètres. Son activation
pour classer les recettes demande une validation suffisante ; la mémoire
exacte reste utilisable avec un petit historique. Les essais sont journalisés
dans `events.jsonl` et leur dernier état est indexé dans `memory.json`.

Ces évolutions ne rendent pas le logiciel capable de calculer les contraintes
mécaniques sans modèle physique. Elles ne fournissent pas un mailleur tout-hexa,
un apprentissage direct du placement de chaque nœud ou une garantie d'optimum.
Le guide [APPRENTISSAGE_CONTINU.md](docs/APPRENTISSAGE_CONTINU.md) décrit les
possibilités et limites ; [VALIDATION.md](docs/VALIDATION.md) définit les preuves
à conserver. Les chiffres de tests doivent être lus dans les rapports réellement
produits avec la livraison.

## Vérifications exécutées sur la rc3

La validation Linux a réussi : **160 tests Python**, aucun test ignoré ;
**62 tests Rust**, aucun échec ni test ignoré. Les six géométries du parcours
continu ont produit **29 essais journalisés** et neuf versions du modèle.
Une nouvelle session a retrouvé puis recalculé la recette gagnante en un essai.
Un seuil impossible a été refusé sans publier de maillage de calcul.

L'interface réelle a exécuté quatre essais, chargé la recette gagnante et
produit un résultat accepté de 1 013 éléments. La boîte d'export a été atteinte.
Ces contrôles ne constituent pas des essais dans les solveurs externes.

**Aucun gain du réseau n'est annoncé** : le modèle de mémoire obtient une erreur
de validation supérieure à la référence constante et reste désactivé pour le
classement. La recherche et la mémoire exacte revalidée sont actives. Les
[résultats et limites](docs/VALIDATION.md) renvoient aux preuves de livraison.

---

# Reconstruction initiale rc2

Cette version remplace le prototype de travail par un parcours de bureau
Python/Qt et un noyau Rust compilé. Le projet Rust d'origine n'était pas
disponible : il s'agit d'une nouvelle implémentation et aucune accélération
par rapport à cet ancien projet n'est mesurée.

## Les trois pages demandées

| Page | Fonctions disponibles |
| --- | --- |
| Géométrie | Import CAO et de peaux triangulées, choix 2D/coque/volume, unités, translation et mise à l'échelle, géométries paramétriques avec trous/congés/chanfreins, préparation et réparation optionnelle |
| Maillage | Taille globale et tailles par entité, ordre 1/2, sélection complète ou par faces/volumes, correspondances périodiques par translation, génération et reprise locale contrôlée |
| Exportation | Profils solveur, conversion numérique des unités dans Rust, groupes de maillage, fichiers individuels et paquet d'échange avec rapports |

La fenêtre comporte une barre de commandes, un navigateur d'entités, une vue
3D avec sélection de faces et des panneaux de paramètres. Elle reprend une
organisation d'atelier CAO, sans reproduire les fonctions complètes de NX.

## Où s'exécutent les traitements

- **Rust** : validation des demandes, conversions, transformations, sélection,
  préparation et pilotage natif du maillage, contrôles numériques/topologiques,
  comparaison des zones conservées, production des exemples d'apprentissage,
  entraînement et inférence du guide expérimental.
- **Gmsh/OpenCASCADE en C/C++ natif** : opérations CAO et algorithmes de maillage,
  appelés depuis Rust par l'API C. Ces bibliothèques ne sont pas réécrites en Rust.
- **Python/Qt** : interface, lancement des processus, lecture des résultats et
  adaptateurs de fichiers. La fenêtre n'appelle pas l'ancien mailleur Python.

Les adaptateurs d'export sont isolés pour qu'une défaillance d'une bibliothèque
native ne ferme pas la fenêtre. L'écriture et la relecture UNV du parcours
natif passent également par le processus Rust.

## Vérifications et limites pratiques

Les tests natifs vérifient notamment les trous, les trois axes après conversion
d'unité, les unités déclarées des STEP, les correspondances nodales, les
coordonnées exportées et la conservation du maillage hors sélection.
Les résultats bruts de livraison figurent dans `verification/native_release/`.

La reprise locale porte sur des **entités géométriques**, avec frontières
conservées. Elle utilise une reconstruction interne vérifiée, dont le coût
est mesuré. L'édition libre cellule par cellule ou le déplacement manuel
arbitraire de nœuds n'est pas implémenté.

Les imports validés sont STEP/STP, IGES/IGS, BREP et des peaux triangulées
STL/MSH/VTK classique/UNV. Un format natif propriétaire de modeleur ou un autre
format ouvert nécessite un adaptateur et sa validation. Les défauts arbitraires
et les intersections complexes ne peuvent pas tous être réparés automatiquement.

Les exports MED, INP, VTU, MSH et UNV sont accompagnés des profils et réserves
dans `docs/FORMATS.md`. Le profil ANSYS est un maillage APDL MESH200, qui demande
ensuite l'affectation d'éléments de calcul. Le pont Cast3M comporte le fichier
UNV et son script d'import. Aucun calcul dans ces solveurs n'a été exécuté ici.

Le programme a été vérifié sous Linux. La compilation et les tests macOS et
Windows sont préparés dans la CI, mais n'ont pas été exécutés depuis cet
environnement. Pour macOS, les sources Rust et le script de construction sont
fournis ; il faut Python et Rust si aucun binaire adapté n'est disponible.

L'apprentissage reste expérimental. Sa chaîne actuelle, de l'évaluation des
stratégies à l'entraînement et à l'inférence, s'exécute en Rust. Le guide
apprend à choisir des algorithmes existants ; il n'invente pas un mailleur
général et ne répare pas automatiquement toutes les pièces industrielles.
