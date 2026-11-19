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
| Cast3M `.dgibi` + `.unv` | Cast3M | Script d'import `LIRE 'UNV'`, à lancer dans le dossier contenant le fichier UNV. |

Les dépendances sont Gmsh 4.x, meshio 5.3.x, numpy et h5py pour MED. Un échec
d'export apparaît dans le manifeste ; l'application ne présente pas cet export
comme réussi. Le ZIP `exports.zip` ne contient que les fichiers effectivement
produits, plus `export_manifest.json` et `groups.json`.

## Choix quadratique dans la rc6

Les blocs directs utilisent par défaut `quadratic_layout: "serendipity"` :
QUAD8, HEX20, PRISM15 et PYRAMID13. `"complete"` demande QUAD9, HEX27,
PRISM18 et PYRAMID14. Le choix doit être compatible avec l'écrivain et avec
la formulation du solveur. Les convertisseurs INP/APDL ci-dessous ne prennent
pas en charge toutes ces variantes ; ils refusent un type incompatible
sans supprimer de nœuds.

La disponibilité d'un mapping d'export n'implique pas qu'une famille peut
être générée sur toute géométrie. Le [périmètre des générateurs directs](EVOLUTIONS_RC6.md)
reste distinct du catalogue des connectivités lisibles ou exportables.

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
- UNV : écriture/relecture Gmsh et nombre d'éléments ; la sémantique des groupes
  du lecteur final doit encore être contrôlée.
- APDL et Gibiane : contrôle structurel des scripts générés. ANSYS, Cast3M,
  Abaqus, CalculiX et Code_Aster n'ont pas été exécutés dans cet environnement.

La compatibilité avec tous les logiciels et toutes leurs versions ne peut pas
être déduite de l'extension du fichier. Commencer par une petite pièce, vérifier
les groupes et les unités, puis suivre les messages du logiciel cible.

## Références officielles

- [Gmsh : formats et API](https://gmsh.info/doc/texinfo/gmsh.html).
- [meshio : formats et code source des écrivains](https://github.com/nschloe/meshio).
- [Code_Aster : maillage MED et Gmsh](https://code-aster.org/doc/v17/manuals/man_u/u1/u1.03.00/Maillage.html).
- [CEA : Débuter avec Cast3M, import UNV](https://www-cast3m.cea.fr/html/formations/Debuter_avec_Cast3M.pdf).
- [ANSYS : référence MESH200](https://ansyshelp.ansys.com/public/Views/Secured/corp/v261/en/ans_elem/Hlp_E_MESH200.html).
