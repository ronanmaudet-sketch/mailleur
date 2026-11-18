# Échanges avec les logiciels de calcul

Un fichier de maillage contient des coordonnées, une connectivité et des
groupes. Il ne remplace pas un modèle de calcul : matériau, formulation des
éléments, épaisseur des coques, unités, chargements, contacts et conditions
limites restent à définir dans le logiciel destinataire.

## Règle des éléments de la rc9

La politique `quad_hex_only` est obligatoire pour les nouveaux résultats et
leurs exports : quadrilatères en 2D/surface, hexaèdres en volume, y compris
les quadrilatères des frontières volumiques. Les points et segments restent
autorisés pour les entités et groupes de bord. Un hexaèdre n'est pas
nécessairement un cube régulier.

Chaque conversion contrôle les types réellement présents dans le fichier
source, même pour une copie `.msh`, sans se fier à une option du projet ou à
un ancien rapport. Les triangles, tétraèdres, prismes et pyramides provoquent
un refus avant d'écraser une destination. Les connectivités avec des nœuds
répétés sont également refusées : une forme dégénérée ne permet pas de
contourner la règle. Le manifeste conserve cette vérification dans
`element_policy.verified_from_actual_cells` et ses effectifs par type.

Un ancien maillage triangulaire ou tétraédrique peut toujours servir d'entrée
au remaillage géométrique. Son export direct est refusé ; il faut générer puis
contrôler un nouveau résultat conforme. Les anciens rapports de validation
rc7 et les références conservées restent des preuves historiques.
Le guide [Quadrilatères et hexaèdres](QUADRILATERES_HEXAEDRES.md) explique
la génération, l'apprentissage et les refus de la nouvelle politique.

## Formats disponibles

| Export | Destination visée | Données et limites |
| --- | --- | --- |
| Gmsh `.msh` | Gmsh et lecteurs Gmsh compatibles | Copie native avec groupes physiques et ordre des éléments conservés. |
| MED `.med` | Code_Aster, Salome-Meca, lecteurs MED, Cast3M selon version | Connectivité de dimensions mélangées. Groupes d'éléments sous forme de familles MED, y compris les groupes qui se recouvrent. |
| Abaqus/CalculiX `.inp` | Import de maillage Abaqus/CalculiX | Seuls les éléments de dimension maximale sont écrits comme éléments de calcul ; les groupes de frontière deviennent des ensembles de nœuds `NSET`. |
| VTK XML `.vtu` | ParaView | Maillage complet ; chaque groupe devient un masque `group:nom`. Visualisation et inspection, sans modèle de calcul. |
| I-DEAS Universal `.unv` | Salome, Cast3M et lecteurs UNV | Écriture et relecture par Gmsh ; vérifier la prise en charge de chaque type d'élément dans le logiciel cible. |
| ANSYS `.dat` | Mechanical APDL | Commandes de nœuds, éléments topologiques `MESH200`, composants d'éléments et de nœuds. Attribuer ensuite les types d'éléments de calcul dans ANSYS. |
| ANSYS structural `.apdl` | Mechanical APDL | Export distinct `ansys_structural`, avec types structuraux explicites selon le mode et les cellules. Matériaux, sections et analyse à compléter. |
| Nastran `.bdf` | Nastran et lecteurs Bulk Data compatibles | Export `nastran` : GRID*, cartes de coques/solides et ensembles SET1 ; propriétés PID et matériaux à définir. |
| LS-DYNA `.key` | LS-DYNA et lecteurs Keyword compatibles | Export `lsdyna` : nœuds, cellules linéaires et ensembles nommés ; définir les PART, SECTION/ELFORM et MAT. |
| Cast3M `.dgibi` + `.unv` | Cast3M | Script d'import `LIRE 'UNV'`, à lancer dans le dossier contenant le fichier UNV. |

Les dépendances sont Gmsh 4.x, meshio 5.3.x, numpy et h5py pour MED. Un échec
d'export apparaît dans le manifeste ; l'application ne présente pas cet export
comme réussi. Le ZIP `exports.zip` ne contient que les fichiers effectivement
produits, plus `export_manifest.json` et `groups.json`.

Le banc Cast3M et l'exemple public proposent un budget mémoire de **1 024 Mio**,
réglable avec `--memory-mib`. La subdivision hexaédrique et l'ordre 2 augmentent
le nombre de nœuds et le coût de résolution ; un budget de mémoire insuffisant
dans Cast3M reste un échec de calcul, même si l'import du maillage est réussi.
Les rapports distinguent ces deux étapes et conservent le budget utilisé.
La commande transmise à Cast3M est `-MEM 1024Mo` : son gestionnaire de mémoire
interprète ici `Mo` avec un facteur de 1 024² octets, conformément à
[sa conversion officielle](https://www-cast3m.cea.fr/index.php?page=sources&source=ooomop).

La cible demandée est l'échange avec les **versions 2020 et ultérieures**.
Les écrivains utilisent des conventions documentées et des types explicites.
Cela ne constitue pas une certification sur chaque logiciel, édition, version
et physique : aucun banc des solveurs commerciaux 2020–2026 n'a été exécuté.
Les formats absents de cette liste ne sont pas annoncés comme disponibles.
Elmer, par exemple, n'a pas d'écrivain dédié dans cette révision ; un parcours
via ElmerGrid demanderait une validation distincte.

## Sélection et matrice de prise en charge

Depuis la racine, après installation :

```sh
.venv/bin/python -m atelier mesh examples/atelier_plate.json \
  -o output/echanges --formats msh,inp,nastran,lsdyna,ansys_structural
```

Les mêmes clés figurent dans `atelier.formats.FORMATS`, dans les choix de
l'interface et dans `GET /api/info`. Vérifier le statut de chaque format et
ses avertissements. Le format historique `ansys` conserve son rôle MESH200
et son extension `.dat` ; le nouveau structural utilise `.apdl`, pour que les
deux sorties coexistent dans le paquet ZIP.

| Cellule source conforme | Nastran BDF | LS-DYNA Keyword | APDL structural |
| --- | --- | --- | --- |
| Quadrilatère 4 nœuds | CQUAD4 | ELEMENT_SHELL | PLANE182 en plan ; SHELL181 en surface |
| Quadrilatère 8 nœuds | CQUAD8 | Refus | Refus : mapping non validé ici |
| Hexaèdre 8 nœuds | CHEXA | ELEMENT_SOLID | SOLID185 |
| Hexaèdre 20 nœuds | CHEXA, 20 nœuds | Refus | Refus : mapping non validé ici |
| Quadrilatère 9 nœuds ou hexaèdre 27 nœuds importé | Refus | Refus | Refus |
| Triangle, tétraèdre, prisme ou pyramide | Refus avant écriture | Refus avant écriture | Refus avant écriture |

La génération rc9 utilise QUA4/HEX8 à l'ordre 1 et les formes incomplètes
QUA8/HEX20 à l'ordre 2. « Quadratique » désigne cet ordre d'interpolation,
pas le nombre de côtés. Les formes complètes QUA9/HEX27 d'un maillage externe
restent soumises aux capacités du format choisi : elles ne sont pas réduites
silencieusement à QUA8/HEX20. Par exemple, VTU conserve QUA9, tandis que
MESH200 le refuse.

Des tables internes conservent des noms de types anciens pour la lecture ou
les contrats historiques ; elles n'autorisent pas leur export par l'API
actuelle. Un résultat conforme au type demandé peut encore être refusé pour
sa qualité, ses contraintes ou une limitation de format.

## Choix de formulation dans INP

Pour un projet plan, l'export sélectionne `CPS4` ou `CPS8` :
**contraintes planes**. Les déformations planes, l'axisymétrie ou un calcul
thermique demandent de modifier la formulation dans le solveur. Une surface
dans l'espace est exportée comme coque `S4` ou `S8R`. Les solides
utilisent `C3D8` ou `C3D20`.
Le fichier précise la formulation retenue dans son manifeste.
Les connectivités C3D20 utilisent des lignes de continuation sans suppression
de nœuds. Les coordonnées respectent les champs réels de vingt caractères.

Les éléments de frontière ne sont pas ajoutés comme poutres ou coques dans un
calcul volumique. Leur groupe devient un ensemble de nœuds. Il n'existe pas de
conversion automatique vers des faces de pression ou des surfaces de contact.
Vérifier la version de CalculiX/Abaqus et les types autorisés pour son analyse.

## ANSYS : topologie et physique séparées

Le format nommé `ansys` de meshio est un format de maillage Fluent. Il n'est
pas utilisé ici pour prétendre produire un modèle Mechanical. Le script APDL
écrit à la place `MESH200`, avec les options de forme documentées par ANSYS :
quadrilatères à 4/8 nœuds et hexaèdres à 8/20 nœuds. Ces éléments
**n'ont pas de degrés de liberté** : le
script importe la topologie, pas un modèle prêt à résoudre. Affecter les types
de calcul adaptés dans Mechanical APDL avant d'effectuer une analyse.

Les groupes de frontière deviennent des composants nodaux préfixés `N_`.
Les triangles, tétraèdres, prismes et pyramides sont refusés par tous les
exports de cette version.

### APDL structural : parcours supplémentaire

`ansys_structural` choisit les types de la matrice. Les projets plans sont
explicitement en **contraintes planes** et doivent être dans XY. PLANE182
reçoit quatre nœuds distincts ; une coque dans l'espace utilise SHELL181.

Les hexaèdres linéaires utilisent SOLID185. Les quadrilatères et hexaèdres
quadratiques sont refusés par `ansys_structural`, dont le mapping de calcul
n'est pas validé ici pour cet ordre. L'export `ansys`/MESH200 conserve leur
topologie, ou un autre format peut être choisi. Aucun ordre 2 n'est transformé
en ordre 1 pour faire réussir un export.

Le script ne crée ni matériau ni section fictive. Définir notamment les
épaisseurs de coques avant résolution. Les composants nodaux et élémentaires
partagent un espace de noms rendu unique ; le manifeste fournit
`verification.component_name_map`. Le script est contrôlé structurellement,
sans exécution ni relecture dans ANSYS.

## Nastran : BDF, ordre et ensembles

`nastran` conserve les éléments de dimension maximale et transforme les
groupes de frontière en ensembles nodaux, comme INP. Les cartes sont
CQUAD4/8 et CHEXA à 8/20 nœuds. Les nœuds intermédiaires de
l'hexaèdre à vingt nœuds sont permutés selon la convention Nastran ; une
relecture meshio doit restituer exactement la connectivité de départ.

Les coordonnées sont écrites en champs **GRID* de 16 caractères**, avec
point décimal et exposant implicite Nastran. La précision est finie : la
relecture contrôle explicitement une erreur relative maximale de `5e-10`
et une tolérance absolue de `1e-300`. Le manifeste conserve ces seuils et
`maximum_coordinate_error`, erreur absolue réellement observée. La sortie
n'est pas annoncée comme identique bit à bit. Cet écrivain ne change pas
les unités : le pont natif a déjà appliqué l'unité d'export choisie.

Les groupes recouvrants deviennent des **SET1**. Une SET1 ne distingue pas
intrinsèquement nœuds et éléments : le contexte du solveur détermine ce sens.
Les commentaires `$ ATELIER_SET` et `verification.set_id_map` conservent
l'identifiant, le nom et le type de chaque ensemble. Les identifiants solveur
commencent à 1 ; les indices `groups.json` sont locaux aux blocs et commencent
à 0. Les identifiants small-field sont limités à 99 999 999.

Un PID par type de cellule est exposé dans `verification.property_id_map`.
Compléter ses PSHELL/PSOLID, matériaux et épaisseurs dans le solveur. Les
appartenances aux groupes ne sont pas confondues avec les propriétés : un
élément peut rester dans plusieurs SET1.

## LS-DYNA : sous-ensemble Keyword linéaire

`lsdyna` écrit `*NODE`, `*ELEMENT_SHELL` ou `*ELEMENT_SOLID`. Il conserve les
cellules de dimension maximale et transforme les groupes de frontière en
ensembles nodaux. Les quadrilatères conservent leurs quatre nœuds et les
hexaèdres leurs huit nœuds. Les formes dégénérées utilisant des répétitions
de nœuds sont refusées par la politique du projet.
Un type quadratique est refusé, sans supprimer ses nœuds intermédiaires.

Chaque type de cellule reçoit un PID, dans `verification.part_id_map`.
L'export est un **fragment de maillage**, sans définition fictive de `*PART`,
`*SECTION_*` ou `*MAT_*`. Ajouter ces cartes et choisir ELFORM dans le modèle
de calcul. Les énergies d'hourglass et la qualité mécanique relèvent de cette étape.
Un domaine 2D sort comme topologie de coque ; cela ne sélectionne pas seul
une formulation de mécanique plane LS-DYNA.

Les groupes nommés utilisent `*SET_NODE_LIST_TITLE`, `*SET_SHELL_LIST_TITLE`
ou `*SET_SOLID_TITLE`. Les recouvrements restent conservés, sans création
automatique de contacts, faces de pression ou PART. Un lecteur interne relit
le sous-ensemble généré et contrôle coordonnées, connectivité et ensembles.
Il n'est pas un lecteur LS-DYNA général et ne remplace pas l'ouverture dans
LS-PrePost ou dans le solveur destinataire.

## Groupes et ordre des éléments

Les groupes physiques Gmsh sont fusionnés par type d'élément lors des
conversions. Cette opération conserve la connectivité et permet l'écriture
MED, dont l'écrivain utilisé n'accepte qu'un bloc par type. Les appartenances
qui se recouvrent sont conservées : un élément peut appartenir à `BOUNDARY`
et à un groupe de face particulier.

Les noms sont convertis en ASCII simple quand le format l'impose et rendus
uniques, sans ambiguïté de casse. Le dictionnaire `group_name_map` du manifeste
conserve la correspondance avec les noms d'origine. `groups.json` conserve les
indices par bloc de type fusionné, avec des indices commençant à zéro. Les
exports solveur renumérotent les éléments ; ces indices ne sont donc pas des
identifiants solveur.

Les éléments quadratiques restent quadratiques. Un type non accepté par un
écrivain provoque une erreur explicite ; aucun export ne supprime les nœuds
intermédiaires pour réussir artificiellement.

## Ce qui est vérifié ici

- Copie MSH identique octet pour octet.
- Relecture MED et VTU, coordonnées, nombres, connectivité et ordre des nœuds.
- Conservation des groupes recouvrants MED et des masques VTU.
- INP : tests de relecture des quadrilatères linéaires et hexaèdres quadratiques,
  distinction éléments/ensembles nodaux, types explicitement choisis.
- UNV/Gibiane : contrôle de relecture des types, de l'ordre et des nombres
  de nœuds/éléments. Le banc `scripts/validate_cast3m.py` vérifie QUA4/QUA8,
  CUB8/CU20, groupes, unités et solution élastique analytique dans Cast3M installé.
- INP : le banc `scripts/validate_calculix.py` vérifie CPS4/CPS8/C3D8/C3D20 en
  m et mm, avec champs numériques bornés à 20 caractères. Les lignes de
  continuation C3D20 ne sont pas comptées comme des éléments supplémentaires.
  Une exécution CalculiX ne certifie pas Abaqus.
- APDL : contrôle structurel des scripts générés. ANSYS, Abaqus et Code_Aster
  n'ont pas été exécutés dans cet environnement.
- BDF : quadrilatères et hexaèdres, relecture meshio, numérotation CHEXA à vingt nœuds,
  précision à plusieurs échelles et SET1 recouvrants/frontières contrôlés.
- Keyword : quadrilatères/hexaèdres linéaires, groupes
  recouvrants et refus des hexaèdres quadratiques sans écrasement de la sortie
  précédente ; relecture par le lecteur interne du sous-ensemble.
- APDL structural : SOLID185, PLANE182 et SHELL181 quadrangulaires,
  composants sans collision et refus des formes non validées.
- Tous les exports, y compris MSH : refus des familles interdites, d'un
  triangle sur la frontière d'un volume hexaédrique et des nœuds répétés,
  même si un rapport associé prétend que le résultat est conforme.

Les contrats supplémentaires sont dans `tests/test_solver_exports.py` et
`tests/test_formats.py`. Aucun solveur commercial cible ni toute la série
2020+ n'ont été exécutés. La [preuve des solveurs rc9](../verification/quad_hex_20261118/solver_README.md)
consigne les cinq cas Cast3M réussis (193 comparaisons), les huit cas CalculiX
réussis et les deux passages réussis du pilote (43 comparaisons chacun), avec
l'empreinte exacte du noyau release. Les calculs Cast3M et CalculiX et les
limites de la rc7 sont conservés dans [VALIDATION_RC7.md](VALIDATION_RC7.md) ;
ils concernaient les anciens triangles/tétraèdres et ne prouvent pas la règle rc9.
Les essais rc9 doivent être lus avec leur révision, leur binaire et leurs
résultats effectifs, sans reprendre les comptages historiques.

La compatibilité avec tous les logiciels et toutes leurs versions ne peut pas
être déduite de l'extension du fichier. Commencer par une petite pièce, vérifier
les groupes et les unités, puis suivre les messages du logiciel cible.

## Références officielles

- [Gmsh : formats et API](https://gmsh.info/doc/texinfo/gmsh.html).
- [meshio : formats et code source des écrivains](https://github.com/nschloe/meshio).
- [Code_Aster : maillage MED et Gmsh](https://code-aster.org/doc/v17/manuals/man_u/u1/u1.03.00/Maillage.html).
- [CEA : Débuter avec Cast3M, import UNV](https://www-cast3m.cea.fr/html/formations/Debuter_avec_Cast3M.pdf).
- [ANSYS : référence MESH200](https://ansyshelp.ansys.com/public/Views/Secured/corp/v261/en/ans_elem/Hlp_E_MESH200.html).
- [ANSYS : SOLID185](https://ansyshelp.ansys.com/public/Views/Secured/corp/v261/en/ans_elem/Hlp_E_SOLID185.html).
- [ANSYS : PLANE182 et triangle K=L](https://ansyshelp.ansys.com/public/Views/Secured/corp/v261/en/ans_elem/Hlp_E_PLANE182.html), [SHELL181 et restriction des triangles](https://ansyshelp.ansys.com/public/Views/Secured/corp/v261/en/ans_elem/Hlp_E_SHELL181.html).
- [LS-DYNA : manuels officiels](https://lsdyna.ansys.com/manuals-download/), [Keyword Volume I R8, conventions ELEMENT_SOLID antérieures à 2020](https://lsdyna.ansys.com/wp-content/uploads/2025/02/LS-DYNA_Manual_Volume_I_R8.0.pdf).
- [PyDYNA, source ANSYS de SET_SOLID](https://github.com/ansys/pydyna/blob/main/src/ansys/dyna/core/keywords/keyword_classes/auto/set/set_solid.py), [SET_SHELL_LIST](https://github.com/ansys/pydyna/blob/main/src/ansys/dyna/core/keywords/keyword_classes/auto/set/set_shell_list.py), [SET_NODE_LIST](https://github.com/ansys/pydyna/blob/main/src/ansys/dyna/core/keywords/keyword_classes/auto/set/set_node_list.py).
- [meshio : lecteur Nastran utilisé pour la relecture](https://github.com/nschloe/meshio/blob/v5.3.5/src/meshio/nastran/_nastran.py). Ces références définissent les conventions, sans prouver une exécution dans chaque version cible.
