# Imports directs : vérification réelle — 7 novembre 2026

**48/48 cas passent**, avec 432 contrôles : lecture, génération du maillage par le noyau Rust, qualité, ordre, unités, coordonnées, mesure géométrique et conservation des sources. La régression dédiée `tests/test_iges_units.py` passe aussi dans les deux suites complètes de 200 tests, sous Python 3.11.18 et 3.13.18 (8 combinaisons réelles par environnement).

## Environnement et portée

Mailleur `1.0.0rc7` ; noyau `mailleur-core 1.0.0-rc.7` ; Python 3.12.16 ; Gmsh 4.15.2 ; meshio 5.3.5 ; macOS 27.2 arm64. Le SHA-256 du noyau final testé (avec correction des boîtes OpenCASCADE) est `39fde2270d7282ebe460591308edebd6d7a978601e519fdddf441ee8d9d9cd85`.

Les fichiers CAO synthétiques sont créés avec Gmsh/OpenCASCADE, les fichiers de maillage avec meshio. Chaque lecture et génération testée est ensuite exécutée par `NativeSession`, dans un vrai processus Rust, sans lecteur simulé. Aucun format propriétaire ni logiciel SolidWorks/CATIA/Creo/Inventor n’est attesté par ces essais.

| Extension | Cas | Ordres | Unités du projet | Particularité |
|---|---:|---|---|---|
| .step | 4 | 1 et 2 | mm et m | CAO volumique, en-tête mm |
| .stp | 4 | 1 et 2 | mm et m | Même STEP standard sous suffixe .stp |
| .iges | 12 | 1 et 2 | mm et m | En-têtes mm et m ; surface, et volume avec couture/construction explicites |
| .igs | 12 | 1 et 2 | mm et m | Même contrôle IGES sous suffixe .igs |
| .brep | 4 | 1 et 2 | mm et m | Coordonnées numériques sans unité ; sources en mm et m |
| .meshb | 4 | 1 et 2 | mm et m | Medit binaire ; tétraèdre reconstruit puis remaillé |
| .nas | 4 | 1 et 2 | mm et m | Nastran ; tétraèdre reconstruit puis remaillé |
| .fem | 4 | 1 et 2 | mm et m | Nastran ; même contrôle sous suffixe .fem |

## Contrôles effectués

- Les géométries sont volontairement décentrées : minimum (7 ; −3 ; 11) mm, maximum (9 ; −2 ; 12) mm. Après import, tous les maillages retrouvent ces coordonnées en mètres, avec une tolérance absolue de 10⁻¹¹ m ; aucune remise à l’origine implicite.
- La boîte CAO mesure 2 × 1 × 1 mm. Son volume remaillé est comparé à 2 × 10⁻⁹ m³, ou son aire extérieure à 10⁻⁵ m² en mode surface. Le tétraèdre des formats de maillage a le même encombrement et un volume de 2 × 10⁻⁹ / 6 m³. Tolérance relative de comparaison : 10⁻⁹.
- Les éléments de domaine sont vérifiés : TRI3/TRI6 en surface, TET4/TET10 en volume. La décision de qualité native doit être admissible.
- Le SHA-256 de chaque fichier original reste inchangé et sa copie conservée dans la session est identique octet pour octet.
- IGES/IGS : les fichiers déclarant des millimètres et ceux déclarant des mètres dans leur en-tête sont croisés avec les unités de projet mm/m. Tous représentent la même géométrie physique ; l’absence de double conversion d’échelle est donc effectivement vérifiée.
- BREP, .meshb, .nas et .fem ne portent pas ici d’unité explicite : deux sources distinctes contiennent réellement des coordonnées en mm ou en m. Le choix d’unité du projet doit correspondre à ces valeurs.

## Défaut trouvé et corrigé : premier import IGES

Avant correction, les 12 essais IGES/IGS de la première campagne échouaient avec `Could not set OpenCASCADE target unit MM/M` ; les 24 autres essais passaient. Le résultat est conservé dans `summary_before_iges_fix.json` et les anciens dossiers de sessions.

Gmsh 4.15.2 appelle la définition de l’unité cible avant de construire son premier lecteur IGES ; le registre d’unités OpenCASCADE n’est alors pas initialisé. Le contournement utilise uniquement l’API publique : première lecture dans un modèle jeté, suppression de ce modèle, création du modèle final, restauration de l’unité cible explicite, puis lecture définitive. L’option globale est restaurée aussi en cas d’échec de l’initialisation. Les coordonnées du premier modèle ne sont jamais utilisées pour le maillage.

Cela impose une lecture CAO supplémentaire aux IGES. Le relevé `iges_bootstrap_timing.json`, sur le fichier de 12 393 octets, mesure environ 29 ms pour la première lecture avec initialisation, 0,2 ms pour supprimer/recréer le modèle et 1,3 ms pour la lecture définitive. Ce petit cas ne prédit pas le coût sur une grosse pièce industrielle.

## Limites et reproduction

Les fichiers IGES produits ici portent six surfaces distinctes. Le mode volume demande explicitement `repair.enabled=true`, `sew=true` et `make_solids=true`. Le banc vérifie alors la construction du volume sur cette enveloppe fermée ; il ne promet pas de fermer automatiquement des trous ou de réparer une CAO arbitraire.

Les fichiers de maillage sont reconstruits à partir de leur peau puis remaillés. Leurs anciens numéros de nœuds et d’éléments, groupes, matériaux et conditions limites ne sont pas transférés. Ces essais ne constituent pas une certification de toutes les versions des logiciels 2020+ ni de tous les éléments possibles.

```sh
.venv/bin/python -m unittest tests.test_iges_units -v
.venv/bin/python scripts/validate_direct_imports.py
```

`summary.json` contient les 48 résultats et les empreintes. Chaque dossier de cas contient `project.json`, `result.json`, les rapports natifs, le maillage produit et une copie du fichier source. `run_final.log` conserve le relevé de la campagne. Les durées sont des mesures ponctuelles avec d’autres vérifications actives sur la machine.

Références primaires : [manuel Gmsh 4.15.2, options et API OpenCASCADE](https://gmsh.info/doc/texinfo/gmsh.html), [code Gmsh 4.15.2 du lecteur IGES](https://raw.githubusercontent.com/live-clones/gmsh/gmsh_4_15_2/src/geo/GModelIO_OCC.cpp), [documentation officielle OpenCASCADE sur IGES](https://github.com/Open-Cascade-SAS/OCCT/wiki/iges).
