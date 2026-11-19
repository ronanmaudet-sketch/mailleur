# Géométries d’entraînement de Mailleur rc9

Les trois modèles distribués avec la rc9 proviennent de la campagne du 18 novembre 2026. Le guide d’algorithmes a examiné quatre pièces, l’apprentissage sur pièce en a examiné deux et le corpus en a examiné 24. Chaque pièce de cette campagne est définie par un projet géométrique synthétique en millimètres. La plaque de 4 × 2 mm figure à la fois dans le guide et dans l’apprentissage sur pièce. Tous les essais d’apprentissage ont utilisé des éléments d’ordre 1. Les essais de génération d’ordre 2 et les calculs Cast3M et CalculiX figurent dans le bilan des vérifications. [Résultats et performances](RESULTATS_MAILLEUR.md)

Le modèle apprend à choisir ou à classer des réglages. Gmsh construit le maillage et le noyau contrôle les éléments obtenus. Les trois modèles publiés restent désactivés pour le guidage automatique. Le calcul des poids a bien eu lieu et les résultats permettent d’expliquer cette décision pour chaque modèle. [Poids distribués et bilan de campagne](../verification/quad_hex_20261118/summary.json)

## Guide de choix d’algorithme

Le guide a comparé les méthodes automatique, Delaunay et frontale sur chaque pièce, avec une taille demandée de 0,65 mm, un minimum de 0,2 mm et une limite de 5 000 éléments. Le tableau compte les quadrilatères qui maillent chaque plaque et les hexaèdres qui maillent chaque pavé.

| Pièce et dimensions en mm | Rôle | Automatique | Delaunay | Frontale |
|---|---|---:|---:|---:|
| Plaque rectangulaire 4 × 2 | Entraînement | 372 | 372 | 336 |
| Pavé 2 × 2 × 1 | Entraînement | 3 840 | 3 840 | 3 976 |
| Plaque carrée 3 × 3, coins de rayon 0,3 | Validation | 666 | 666 | 564 |
| Pavé 2 × 1 × 1 | Validation | Refus, 5 096 prévus | Refus, 5 096 prévus | Refus, 5 872 prévus |

Douze évaluations ont produit neuf maillages. Le guide a effectué douze époques d’entraînement. Son score moyen en validation et celui de l’algorithme fixe de référence valent tous deux 0,32448. Dans l’essai de génération distinct avec un plafond de 10 000, le même pavé de 2 × 1 × 1 mm a donné 5 096 hexaèdres sur le Mac. [Poids et décision du guide](../verification/quad_hex_20261118/models/historique.json)

## Apprentissage sur une pièce confiée au mailleur

La campagne a appelé deux fois la fonction d’apprentissage. Pour chaque plaque, elle a essayé les méthodes automatique et Delaunay, avec une taille de 0,65 mm et un plafond de 10 000 éléments.

| Pièce et dimensions en mm | Rôle | Automatique | Delaunay |
|---|---|---:|---:|
| Plaque rectangulaire 4 × 2 | Entraînement | 372 quadrilatères acceptés | 372 quadrilatères acceptés |
| Plaque rectangulaire 5 × 2 | Entraînement | 306 quadrilatères acceptés | 306 quadrilatères acceptés |

Les quatre observations ont servi au calcul des poids. Les deux modes de calcul donnent le même score sur chaque plaque. Le logiciel exige au moins trois groupes géométriques pour réserver un groupe à la validation. Cette campagne en compte deux, d’où l’absence de validation indépendante pour ce modèle. [Poids et décision du modèle sur pièce](../verification/quad_hex_20261118/models/experience.json)

## Corpus de 24 géométries

Le générateur a pris un exemplaire dans chacune des 24 familles, avec la graine 42 et sans tirage aléatoire supplémentaire. Seize familles ont servi au calcul des poids, quatre à la validation pour choisir les poids et quatre au test final. Les réglages comparés étaient la méthode automatique avec un passage de lissage, puis Delaunay avec cinq passages, à taille demandée identique pour une même pièce. [Définition des familles et des stratégies](../rust_core/src/corpus_generator.rs)

Dans les deux dernières colonnes, un nombre seul signifie que le maillage a satisfait les contrôles. « Qualité » signale un maillage produit dont les critères d’acceptation ont échoué. « Refus » signifie qu’aucun maillage final n’a été produit. Les nombres portent sur les quadrilatères du domaine pour un plan ou une surface, et sur les hexaèdres du domaine pour un volume. Les cotes sont arrondies à trois décimales. « R » désigne un rayon. Le fichier de mesures contient les valeurs originales, les coordonnées des trous et des sommets et les paramètres de chaque projet. [Données des trois modèles](../verification/quad_hex_20261118/geometries_entrainement.json)

| N° | Géométrie, mode et dimensions en mm | Rôle | Automatique | Delaunay |
|---:|---|---|---:|---:|
| 0 | Plan, plaque 10,353 × 8,526 à un trou R 0,534 | Entraînement | 984 | 984 |
| 1 | Plan, plaque 4,235 × 6 à deux trous R 0,318 | Entraînement | 1 704 | 1 704 |
| 2 | Plan, plaque 5,176 × 6,316 à coins arrondis R 0,660 | Entraînement | 972 | 972 |
| 3 | Plan, plaque 5,176 × 4,737 à chanfreins de 0,359 | Validation | 450 | 450 |
| 4 | Plan, profil concave en L de 6,118 × 7,895 | Entraînement | 360 | 360 |
| 5 | Plan, polygone 11,765 × 3,789 à ouverture rectangulaire | Test | 1 008 | 1 008 |
| 6 | Volume, plaque mince 8,941 × 4,737, épaisseur 0,219 | Entraînement | 2 852, qualité | 2 792, qualité |
| 7 | Volume, plaque 8,941 × 7,895 × 2,609, un trou R 0,480 | Entraînement | 11 548 | 11 472 |
| 8 | Volume, équerre de largeur 5,176, hauteur 4,105, profondeur 3,652, paroi 1,026 et trou R 0,246 | Entraînement | 9 772 | Refus |
| 9 | Surface d’une sphère R 4,141 | Entraînement | 996 | 1 008 |
| 10 | Surface d’un pavé 10,353 × 3,158 × 3,130 | Validation | 5 454 | 5 454 |
| 11 | Surface plane, plaque 9,882 × 8,526 à un trou R 0,754 et coins R 0,597 | Test | 2 118 | 2 118 |
| 12 | Volume, pavé 4,235 × 5,053 × 2,609 avec zone de qualité imposée | Entraînement | 5 236, qualité | 5 220, qualité |
| 13 | Volume, sphère pleine R 2,965 | Test | 14 744 | 14 624 |
| 14 | Plan, cadre rectangulaire 4,235 × 3,789 à bord de 0,474 | Entraînement | 288 | 288 |
| 15 | Volume, équerre de largeur 4,706, hauteur 7,579, profondeur 4,435 et paroi 0,399 | Validation | 4 048, qualité | 4 072, qualité |
| 16 | Surface d’un pavé mince 6,588 × 8,526 × 0,166 | Entraînement | 1 440 | 1 440 |
| 17 | Plan, polygone auto-intersecté de 8,471 × 3,789 | Entraînement | Refus | Refus |
| 18 | Plan, plaque 8 × 5,368 à deux trous R 0,805, intervalle de 0,051 entre bords | Entraînement | Refus | Refus |
| 19 | Plan, polygone 8 × 3,474 avec une arête oblique de 0,031 | Validation | 771 | 771 |
| 20 | Plan, profil en escalier de 6,588 × 7,579 | Entraînement | 447 | 447 |
| 21 | Plan, plaque 4,706 × 7,895 à un trou R 0,359 et petits congés R 0,101 | Test | 1 737 | 1 737 |
| 22 | Volume, plaque 8,941 × 5,684 × 4,174 à quatre trous R 0,204 | Entraînement | Refus | Refus |
| 23 | Surface plane, plaque 10,353 × 6 à deux trous R 0,444 et chanfreins de 0,180 | Entraînement | 1 896 | 1 896 |

Les modes déclarés sont 12 plans, 5 surfaces et 7 volumes. Les familles 11 et 23 sont des plaques en mode surface avec une seule face plane, sans épaisseur modélisée. La famille 16 maille les faces d’un pavé dont l’épaisseur est effectivement modélisée. Les zones de « contact », « interface » ou « contrainte » de certains projets sont des régions désignées par le générateur pour évaluer la qualité locale. Aucune solution mécanique n’a servi à les identifier. [Paramètres des projets](../verification/quad_hex_20261118/geometries_entrainement.json)

### Résultats et motifs de refus

Sur 48 essais, 41 ont produit un maillage. Parmi ces 41 maillages, **35 ont satisfait les contrôles et 6 ont échoué aux critères de qualité**. Les 7 autres essais se sont arrêtés avant la production du maillage. Cela représente **18 pièces sur 24** avec au moins une stratégie admissible, dont l’équerre percée qui réussit seulement avec la première stratégie. Les 70 évaluations du noyau comptabilisent aussi les inspections initiales. Le polygone auto-intersecté a été éliminé dès l’inspection.

| Cas | Cause observée sur ces pièces et ces réglages |
|---|---|
| 6, plaque mince | Les deux stratégies passent sous le seuil global de qualité de 0,03 et sous le seuil local de 0,30. |
| 8, équerre percée | Delaunay produit au moins un élément inversé ou dégénéré. La méthode automatique réussit. |
| 12 et 15, pavé et équerre mince | Respectivement 10 à 11 et 23 à 25 éléments passent sous le seuil local de 0,30, malgré une qualité minimale globale positive. |
| 17, polygone auto-intersecté | Le contour qui se croise est un défaut volontaire du jeu de données. |
| 18, trous rapprochés | Les deux stratégies produisent des éléments inversés ou dégénérés. |
| 22, plaque à quatre trous | Les deux subdivisions prévoient 94 296 et 90 684 hexaèdres. Le budget fixé à 50 000 entraîne un refus avant subdivision. |

Le modèle de corpus a reçu 32 observations pour l’entraînement, 8 pour la validation et 8 pour le test. Les essais échoués ou refusés ont reçu un score cible nul. En validation, la stratégie choisie par le modèle et la stratégie automatique obtiennent chacune 3 pièces admissibles sur 4, avec le même score moyen de 0,49814. Sur les quatre familles du test final, chacune obtient 4 sur 4 et le même score moyen de 0,71610. Cette campagne justifie le fonctionnement des contrôles sur ces cas et laisse ouverte la question du gain sur d’autres pièces. [Poids et critères du modèle de corpus](../verification/quad_hex_20261118/models/corpus.json)

## Campagnes antérieures

Les poids historiques conservés dans le dépôt viennent de campagnes distinctes. La rc9 exclut les observations historiques incompatibles du nouvel entraînement. Aucun fichier CAO fourni par un utilisateur n’apparaît dans les projets des trois modèles de la campagne rc9. L’ancien pilote sur pièce comprenait un exemple STEP à deux volumes.

- L’ancien guide a utilisé 16 configurations sur huit familles. Les familles d’entraînement étaient la plaque percée, la plaque à congés, le pavé en surface, le pavé plein et l’équerre pleine. La validation regroupait le contour concave, la surface sphérique et la sphère pleine. [Guide historique et familles](../verification/native_release/runs/native/07bab630e29344ddbfb69ea30c947b7d/guide.json)
- L’ancien modèle sur pièce contenait six groupes géométriques. Cinq ont servi à l’entraînement, avec deux plaques percées (une à congés et une chanfreinée), un contour concave percé, une surface sphérique et l’exemple STEP à deux volumes. L’équerre volumique percée formait le groupe de validation. Il y avait 23 observations d’entraînement et 4 de validation. [Modèle historique sur pièce](../verification/continuous_learning/continuous_runs/20261104T091837738488/experience/model.json)
- L’ancien pilote de corpus comprenait 48 projets issus des mêmes 24 familles nommées, avec une configuration structurée et une configuration aléatoire par famille. Son découpage comptait 32 projets d’entraînement, 8 de validation et 8 de test. [Modèle du pilote historique et découpage par famille](../verification/corpus/pilot/20261104T091842739497/models/current.json)

## Reproduire la campagne rc9

Depuis la racine de la rc9, dans son environnement Python avec le noyau et Gmsh installés, utiliser deux répertoires de sortie neufs. La commande rejoue les trois entraînements, écrit les journaux par projet et exporte les trois fichiers de poids. Les durées et les nombres exacts d’éléments peuvent varier entre machines. [Script de reproduction](../scripts/validate_quad_hex_learning.py)

```sh
python3 scripts/validate_quad_hex_learning.py --output essai-rc9 --evidence resultats-rc9
```
