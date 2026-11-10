# Échanges avec les logiciels de calcul

Un fichier de maillage contient des coordonnées, une connectivité et des
groupes. Il ne remplace pas un modèle de calcul : matériau, formulation des
éléments, épaisseur des coques, unités, chargements, contacts et conditions
limites restent à définir dans le logiciel destinataire.

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

| Cellule source | Nastran BDF | LS-DYNA Keyword | APDL structural |
| --- | --- | --- | --- |
| Triangle 3 nœuds | CTRIA3 | ELEMENT_SHELL, N3=N4 | PLANE182, K=L, mode plan seulement |
| Triangle 6 nœuds | CTRIA6 | Refus | Refus : mapping non validé ici |
| Quadrilatère 4 nœuds | CQUAD4 | ELEMENT_SHELL | PLANE182 en plan ; SHELL181 en surface |
| Quadrilatère 8 nœuds | CQUAD8 | Refus | Refus : mapping non validé ici |
| Tétraèdre 4 nœuds | CTETRA | ELEMENT_SOLID, N4 répété | SOLID285, déplacement pur, KEYOPT(1)=1 |
| Tétraèdre 10 nœuds | CTETRA, 10 nœuds | Refus | SOLID187 |
| Hexaèdre 8 nœuds | CHEXA | ELEMENT_SOLID | SOLID185 |
| Hexaèdre 20 nœuds | CHEXA, 20 nœuds | Refus | Refus : mapping non validé ici |
| Prisme 6 nœuds | CPENTA | ELEMENT_SOLID, N5=N6 et N7=N8 | Refus |
| Autres, dont pyramides, quad9 et prisme15 | Refus | Refus | Refus |

Cette matrice décrit les écrivains, pas les capacités du générateur. Elle
n'annonce pas que le moteur sait produire toutes ces formes sur une pièce
quelconque. Certains contrats de connectivité utilisent des cellules de
référence ; les tests d'intégration utilisent aussi des maillages Gmsh réels.

## Choix de formulation dans INP

Pour un projet plan, l'export sélectionne `CPS3`, `CPS4`, `CPS6` ou `CPS8` :
**contraintes planes**. Les déformations planes, l'axisymétrie ou un calcul
thermique demandent de modifier la formulation dans le solveur. Une surface
dans l'espace est exportée comme coque `S3`, `S4`, `S6` ou `S8R`. Les solides
utilisent des éléments `C3D*`, notamment `C3D4` et `C3D10` pour les tétraèdres.
Le fichier précise la formulation retenue dans son manifeste.
Les pyramides linéaires utilisent `C3D5` pour Abaqus ; cet élément n'est pas
présumé compatible avec CalculiX et un avertissement accompagne cet export.

Les éléments de frontière ne sont pas ajoutés comme poutres ou coques dans un
calcul volumique. Leur groupe devient un ensemble de nœuds. Il n'existe pas de
conversion automatique vers des faces de pression ou des surfaces de contact.
Vérifier la version de CalculiX/Abaqus et les types autorisés pour son analyse.

## ANSYS : topologie et physique séparées

Le format nommé `ansys` de meshio est un format de maillage Fluent. Il n'est
pas utilisé ici pour prétendre produire un modèle Mechanical. Le script APDL
écrit à la place `MESH200`, avec les options de forme documentées par ANSYS :
triangles à 3/6 nœuds, quadrilatères à 4/8 nœuds, tétraèdres à 4/10 nœuds et
hexaèdres à 8/20 nœuds. Ces éléments **n'ont pas de degrés de liberté** : le
script importe la topologie, pas un modèle prêt à résoudre. Affecter les types
de calcul adaptés dans Mechanical APDL avant d'effectuer une analyse.

Les groupes de frontière deviennent des composants nodaux préfixés `N_`.
Les prismes et pyramides sont refusés par cet export plutôt que transformés
silencieusement ou tronqués. Les autres formats peuvent les accepter.

### APDL structural : parcours supplémentaire

`ansys_structural` choisit les types de la matrice. Les projets plans sont
explicitement en **contraintes planes** et doivent être dans XY. Le triangle
linéaire PLANE182 répète K et L, conformément à la référence ANSYS. Pour une
coque dans l'espace, seul le quadrilatère SHELL181 est accepté : ANSYS réserve
sa forme triangulaire dégénérée au remplissage, ce qui ne justifie pas d'en
faire automatiquement tout un domaine triangulé.

SOLID285 est réglé en déplacement pur, pour élasticité linéaire ou très
petites déformations plastiques. Ne pas reprendre cette formulation sans
examen pour un matériau quasi incompressible ou une grande plasticité.
Adapter la formulation à l'analyse. Les tétraèdres quadratiques conservent
leurs dix nœuds dans SOLID187 et les hexaèdres linéaires utilisent SOLID185.
Les autres formes sont refusées sans approximation cachée.

Le script ne crée ni matériau ni section fictive. Définir notamment les
épaisseurs de coques avant résolution. Les composants nodaux et élémentaires
partagent un espace de noms rendu unique ; le manifeste fournit
`verification.component_name_map`. Le script est contrôlé structurellement,
sans exécution ni relecture dans ANSYS.

## Nastran : BDF, ordre et ensembles

`nastran` conserve les éléments de dimension maximale et transforme les
groupes de frontière en ensembles nodaux, comme INP. Les cartes sont
CTRIA3/6, CQUAD4/8, CTETRA, CHEXA et CPENTA. Les nœuds intermédiaires de
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
ensembles nodaux. Les triangles utilisent N1,N2,N3,N3 ; les tétraèdres
N1,N2,N3,N4,N4,N4,N4,N4 ; les prismes N1,N2,N3,N4,N5,N5,N6,N6,
conformément au manuel Keyword. Les hexaèdres conservent leurs huit nœuds.
Un type quadratique est refusé, sans supprimer ses nœuds intermédiaires.

Chaque type de cellule reçoit un PID, dans `verification.part_id_map`.
L'export est un **fragment de maillage**, sans définition fictive de `*PART`,
`*SECTION_*` ou `*MAT_*`. Ajouter ces cartes et choisir ELFORM dans le modèle
de calcul. Le choix entre tétraèdre dégénéré et formulation tétraédrique,
les énergies d'hourglass et la qualité mécanique relèvent de cette étape.
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
- INP : tests de relecture des triangles linéaires et tétraèdres quadratiques,
  distinction éléments/ensembles nodaux, types explicitement choisis.
- UNV/Gibiane : cinq cas réellement importés et calculés dans Cast3M 2026.0
  arm64, ordres 1/2, avec contrôle des groupes, unités et solution analytique.
- INP : huit calculs réels dans CalculiX 2.23, CPS3/CPS6/C3D4/C3D10, en m et mm ;
  champs numériques bornés à 20 caractères. Cela ne certifie pas Abaqus.
- APDL : contrôle structurel des scripts générés. ANSYS, Abaqus et Code_Aster
  n'ont pas été exécutés dans cet environnement.
- BDF : neuf types, relecture meshio, numérotation CHEXA à vingt nœuds,
  précision à plusieurs échelles et SET1 recouvrants/frontières contrôlés.
- Keyword : cinq formes linéaires, répétitions nodales documentées, groupes
  recouvrants et refus des tétraèdres quadratiques sans écrasement de la sortie
  précédente ; relecture par le lecteur interne du sous-ensemble.
- APDL structural : SOLID285/187/185, PLANE182 et SHELL181 quadrangulaire,
  composants sans collision et refus des formes non validées.

Les contrats supplémentaires sont dans `tests/test_solver_exports.py` et
`tests/test_formats.py`. Aucun solveur commercial cible ni toute la série
2020+ n'ont été exécutés. Les calculs Cast3M et CalculiX ainsi que leurs
limites sont détaillés dans [VALIDATION_RC7.md](VALIDATION_RC7.md).

La compatibilité avec tous les logiciels et toutes leurs versions ne peut pas
être déduite de l'extension du fichier. Commencer par une petite pièce, vérifier
les groupes et les unités, puis suivre les messages du logiciel cible.

## Références officielles

- [Gmsh : formats et API](https://gmsh.info/doc/texinfo/gmsh.html).
- [meshio : formats et code source des écrivains](https://github.com/nschloe/meshio).
- [Code_Aster : maillage MED et Gmsh](https://code-aster.org/doc/v17/manuals/man_u/u1/u1.03.00/Maillage.html).
- [CEA : Débuter avec Cast3M, import UNV](https://www-cast3m.cea.fr/html/formations/Debuter_avec_Cast3M.pdf).
- [ANSYS : référence MESH200](https://ansyshelp.ansys.com/public/Views/Secured/corp/v261/en/ans_elem/Hlp_E_MESH200.html).
- [ANSYS : SOLID285](https://ansyshelp.ansys.com/public/Views/Secured/corp/v261/en/ans_elem/Hlp_E_SOLID285.html), [SOLID187](https://ansyshelp.ansys.com/public/Views/Secured/corp/v261/en/ans_elem/Hlp_E_SOLID187.html), [SOLID185](https://ansyshelp.ansys.com/public/Views/Secured/corp/v261/en/ans_elem/Hlp_E_SOLID185.html).
- [ANSYS : PLANE182 et triangle K=L](https://ansyshelp.ansys.com/public/Views/Secured/corp/v261/en/ans_elem/Hlp_E_PLANE182.html), [SHELL181 et restriction des triangles](https://ansyshelp.ansys.com/public/Views/Secured/corp/v261/en/ans_elem/Hlp_E_SHELL181.html).
- [LS-DYNA : manuels officiels](https://lsdyna.ansys.com/manuals-download/), [Keyword Volume I R8, conventions ELEMENT_SOLID antérieures à 2020](https://lsdyna.ansys.com/wp-content/uploads/2025/02/LS-DYNA_Manual_Volume_I_R8.0.pdf).
- [PyDYNA, source ANSYS de SET_SOLID](https://github.com/ansys/pydyna/blob/main/src/ansys/dyna/core/keywords/keyword_classes/auto/set/set_solid.py), [SET_SHELL_LIST](https://github.com/ansys/pydyna/blob/main/src/ansys/dyna/core/keywords/keyword_classes/auto/set/set_shell_list.py), [SET_NODE_LIST](https://github.com/ansys/pydyna/blob/main/src/ansys/dyna/core/keywords/keyword_classes/auto/set/set_node_list.py).
- [meshio : lecteur Nastran utilisé pour la relecture](https://github.com/nschloe/meshio/blob/v5.3.5/src/meshio/nastran/_nastran.py). Ces références définissent les conventions, sans prouver une exécution dans chaque version cible.
