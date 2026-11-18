# Maillages exclusivement quadrilatères et hexaèdres — rc9

Depuis la rc9 du 18 novembre 2026, les maillages de calcul acceptés par
Mailleur respectent la politique `quad_hex_only`. Elle s'applique à la
génération, à la recherche sur une pièce, au corpus de maillages, à
l'entraînement des guides et aux exports. Si la géométrie, la qualité ou les
ressources ne permettent pas de respecter cette politique, le traitement est
refusé avec un diagnostic. Aucun triangle ou tétraèdre n'est livré comme
substitut accepté.

## Formes et ordre des éléments

| Domaine | Éléments produits, ordre 1 | Éléments produits, ordre 2 |
| --- | --- | --- |
| Plan 2D ou surface/coque géométrique | Quadrilatères à 4 nœuds | Quadrilatères à 8 nœuds |
| Volume 3D | Hexaèdres à 8 nœuds | Hexaèdres à 20 nœuds |
| Frontières d'un volume | Quadrilatères à 4 nœuds | Quadrilatères à 8 nœuds |
| Arêtes et groupes de frontière | Segments à 2 nœuds | Segments à 3 nœuds |

Les segments et éventuels points de repérage décrivent des frontières ou des
groupes ; ils ne remplacent pas les quadrilatères ou hexaèdres du domaine.
Chaque format reste limité aux ordres et groupes qu'il sait écrire sans
réduction silencieuse : voir [FORMATS.md](FORMATS.md).

Un quadrilatère possède quatre côtés ; ce n'est pas nécessairement un carré.
Un hexaèdre possède six faces quadrilatérales ; ce n'est pas nécessairement un
cube régulier. Imposer des cubes parfaits sur toutes les pièces courbes serait
une autre exigence géométrique. « Quadratique » signifie ordre 2, avec des
nœuds supplémentaires : ce mot ne désigne pas une forme quadrilatérale.

## Construction du maillage

Python transporte le projet et les fichiers. Le noyau Rust pilote les appels
natifs Gmsh/OpenCASCADE, construit le maillage et calcule sa qualité.

1. La CAO est préparée et les contraintes de taille sont appliquées.
2. Gmsh construit un maillage intermédiaire, qui peut contenir des triangles
   ou des tétraèdres. Ces cellules servent au calcul interne de subdivision.
3. Le noyau compte le résultat attendu avant de subdiviser. Un triangle donne
   trois quadrilatères ; un tétraèdre donne quatre hexaèdres. Les quadrilatères
   et hexaèdres déjà présents se subdivisent également.
4. La subdivision native produit le maillage final. L'ordre 2 emploie les
   variantes à 8 et 20 nœuds pour faciliter les échanges avec les solveurs.
5. Le noyau vérifie les familles de cellules sur les surfaces, les volumes et
   leurs frontières, puis la qualité et les contraintes du projet.
6. Seul un résultat admissible devient le maillage exportable ou un exemple
   d'apprentissage accepté.

La [documentation Gmsh 4.15.2](https://gmsh.info/doc/texinfo/gmsh.html#Mesh-module)
décrit la subdivision tout quadrilatères/tout hexaèdres. Il s'agit ici d'un
maillage issu d'une subdivision, et pas d'une décomposition structurée de toute
pièce industrielle en blocs réguliers. Le coût et la qualité dépendent du
maillage intermédiaire et de la géométrie.

Les primitives CAO sont construites directement dans les unités internes SI ;
les imports STEP/IGES demandent également des mètres au lecteur natif. Les
fichiers d'origine restent inchangés. Pour une source discrète telle qu'un STL,
la subdivision interpole sa peau polygonale : elle ne retrouve pas une surface
CAO analytique absente du fichier. Cette approximation est signalée dans le
rapport et ses avertissements.

L'aperçu peut découper graphiquement un quadrilatère en deux triangles pour
l'affichage. L'import d'un ancien maillage peut aussi préparer une peau
triangulée pour reconstruire la géométrie. Ces représentations ne sont pas les
éléments de calcul acceptés. Le contrôle porte sur le fichier final, avant
export et après les relectures prévues par chaque écrivain.

## Utilisation et API

L'interface indique « Quadrilatères uniquement » pour un plan ou une surface,
et « Hexaèdres uniquement » pour un volume. Il n'y a pas de case permettant
d'accepter un mélange. Choisir ensuite la taille cible, l'ordre et les seuils
de qualité adaptés au calcul.

Dans le JSON d'un projet, la règle est explicite :

```json
{
  "schema_version": 1,
  "name": "Boîte hexaédrique",
  "mode": "volume",
  "units": {"input": "mm", "internal": "m"},
  "geometry": {"kind": "box", "width": 2, "height": 1, "depth": 1},
  "mesh": {
    "element_policy": "quad_hex_only",
    "size": 0.5,
    "min_size": 0.2,
    "order": 1,
    "algorithm": "automatic",
    "threads": 1,
    "max_elements": 10000
  }
}
```

Le champ omis est normalisé vers `quad_hex_only`, y compris pour une requête
directe au noyau Rust. Une autre valeur est refusée. L'ancien booléen
`recombine` ne permet plus d'autoriser un maillage mixte ; la subdivision et
le contrôle final restent obligatoires.

La commande `python -m atelier mesh projet.json -o output/boite --formats msh,inp`
et `POST /api/jobs` utilisent le même contrat. Pour l'API, placer ce projet
dans le champ `project` et suivre le calcul comme décrit dans
[API_UTILISATION.md](API_UTILISATION.md). Les refus de qualité ou de type
d'élément doivent être traités comme des échecs du calcul.

Le rapport JSON contient `element_policy.requested`, `verified`, `dimension`
et `final_cell_counts`. Ces comptes distinguent `quadrilateral`, `hexahedron`
et `segment` sur le maillage réel. `cell_counts` conserve en complément les
noms détaillés Gmsh, notamment l'ordre des éléments. Un ancien binaire sans
cette attestation est refusé par le pont Python : mettre à jour ou recompiler
le noyau avec les sources de la même version.

## Apprentissage et anciennes données

Le guide apprend à comparer des recettes de maillage : algorithme, tailles et
raffinements. Il ne génère pas directement la position de chaque nœud. Les
essais sont réellement calculés puis contrôlés selon la règle quadrilatères/
hexaèdres avant d'être retenus comme exemples acceptés.

Fournir une pièce à `generate` ne réentraîne pas systématiquement un réseau.
Utiliser « Apprendre sur cette pièce », la commande `learn` ou
`learning.enabled: true` pour activer la recherche et la mise à jour de la
mémoire. La génération de corpus et l'entraînement sont également deux
opérations distinctes : planifier un million de cas ne les calcule pas.

Les anciens corpus et modèles ne sont pas réétiquetés en tout quadrilatères/
hexaèdres. Les anciennes observations restent conservées mais sont exclues
du nouvel entraînement si la provenance et les contrôles requis manquent.
Un ancien modèle ne guide pas la nouvelle génération. Recalculer les pièces
et réentraîner sur les résultats stricts ; un nouveau dossier de travail évite
de mélanger les campagnes. Les anciennes preuves sous `verification/` et la
référence Cast3M rc7 décrivent leur version historique.

Le schéma des caractéristiques de production est désormais
`atelier.geometry_action_features.v3`. La politique figure dans les recettes,
identités, enregistrements et modèles. Les tests de performance purement
mathématiques peuvent entraîner un réseau sur des tableaux synthétiques
explicitement marqués sans maillage ; leurs poids sont non déployables et
ne servent pas de guide au mailleur.

## Coût, refus et améliorations possibles

La taille cible règle le maillage intermédiaire ; elle n'impose pas la même
longueur à toutes les arêtes finales. La subdivision augmente le nombre
d'éléments et de nœuds, puis l'ordre 2 ajoute des nœuds. Le nombre réel est
vérifié avant subdivision et la limite finale reste applicable. Mesurer le
temps, la mémoire et la qualité sur les pièces visées avant une grande
campagne d'apprentissage.

Le remaillage local est actuellement refusé : la subdivision globale pourrait
modifier des cellules hors sélection. Régénérer toute la pièce permet de
respecter la règle de forme ; cela ne doit pas être présenté comme un
remaillage conservant l'extérieur. Les fichiers du résultat précédent restent
intacts.

Les prochaines améliorations utiles sont les blocs structurés pour des pièces
adaptées, l'extrusion de quadrilatères, la maîtrise de la projection sur les
surfaces courbes, l'optimisation mesurée des hexaèdres et un remaillage local
qui conserve réellement les interfaces. Elles doivent conserver les contrôles
de forme, de Jacobiennes, de qualité, de groupes et de périodicité. Les
résultats dans un solveur réel doivent rester distingués d'une simple relecture
de fichier ; les essais ne certifient pas tous les solveurs et toutes les
versions depuis 2020.
