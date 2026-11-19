# rc6 : éléments directs et échanges avec les solveurs

Les chemins de rapports rc3 à rc6 ci-dessous désignent les fichiers de l’archive d’origine. Pour les contrôles de la version actuelle, consulter [le bilan rc10](../verification/rc10/README.md).

Cette note décrit l’archive amont rc6. La version active et ses réglages figurent dans le [README](../README.md).

La rc6 donne la priorité aux quadrilatères et aux hexaèdres structurés,
à l'ordre 2 et à leur utilisation depuis un script. Les formes demandées
explicitement ne sont pas remplacées par une autre famille pour terminer
un calcul. Une géométrie ou un réglage incompatible produit un diagnostic.

Le noyau Rust construit les nouvelles connectivités structurées et leurs
nœuds d'ordre 2. Gmsh sert à leur classification sur la CAO, aux contrôles
numériques et à l'écriture des fichiers natifs. Python conserve l'interface,
les échanges de fichiers et l'orchestration des appels.

## Forme, ordre et répartition des tailles

Ces choix restent indépendants : un hexaèdre est une forme d'élément,
« quadratique » désigne son ordre d'interpolation, et une progression de
tailles décrit la répartition de la discrétisation dans le domaine.

Les nouveaux projets utilisent l'ordre 2 par défaut. Un ancien projet
contenant explicitement `order: 1` conserve ce choix. Les ordres 3 et
supérieurs ne sont pas proposés.

| Choix `mesh.element_shape` | Parcours |
| --- | --- |
| `automatic` | En rc10, quadrilatères directs en surface et hexaèdres directs en volume sur les blocs compatibles. La rc6 amont utilisait les algorithmes classiques pour ce choix. |
| `triangle` | Famille triangulaire explicite ; parcours classique ou direct avec paramètres structurés |
| `tetrahedron` | Famille tétraédrique explicite ; parcours classique ou direct avec paramètres structurés |
| `quadrilateral` | Cellules quadrilatères construites directement sur des blocs compatibles |
| `hexahedron` | Cellules hexaédriques construites directement sur des blocs compatibles |
| `prism` | Prismes triangulaires construits directement sur des blocs compatibles |
| `pyramid` | Pyramides construites directement sur des blocs compatibles |

Le parcours structuré Rust émet les connectivités de la famille demandée.
Pour l'ordre 2, il calcule directement les nœuds intermédiaires nécessaires ;
il ne produit pas d'abord un fichier ou un maillage linéaire à convertir.
Les maillages intermédiaires et la recombinaison ne font pas partie de ce
parcours. Le rapport expose notamment `recombination_used`,
`order_elevation_used` et `intermediate_mesh_generated`.

Pour les prismes et pyramides, la disposition suit un découpage logique du
bloc. Aucune collection d'éléments hexaédriques ou tétraédriques n'est
maillée puis fusionnée pour fabriquer la famille demandée. Cela ne rend pas
ces cellules équivalentes à une grille tensorielle tout-hexa.

La recombinaison est désormais refusée explicitement. Un projet ancien qui
la demandait doit être revu : retirer ce réglage ne suffit pas à obtenir un
maillage quadrilatère sur une CAO incompatible avec les blocs disponibles.

## Périmètre géométrique des blocs directs

Les primitives compatibles comprennent :

- une plaque rectangulaire sans congé ni chanfrein ;
- un polygone strictement convexe à quatre sommets, sans trou ;
- une boîte, en volume ou comme surface fermée ;
- une plaque rectangulaire comportant exactement un trou circulaire
  strictement intérieur, centré ou décentré ;
- l'extrusion de ces empreintes planes pour les domaines volumiques.

Le trou utilise une grille en anneau découpée en quatre blocs, appelée
O-grid. Les nœuds de frontière, y compris les nœuds quadratiques, suivent
le cercle. Les raccords des blocs partagent leurs nœuds.

Les imports CAO généraux, plusieurs trous, les congés et chanfreins, les
formes concaves ou les assemblages complexes demandent encore une
stratégie de découpage en blocs plus générale. Ils ne deviennent pas
compatibles avec le tout-hexa par la seule sélection de ce menu. Les
parcours classiques de triangles et de tétraèdres restent disponibles sur
les géométries que leur moteur peut traiter.

La génération peut porter sur des entités compatibles sélectionnées, par
exemple une seule face d'une boîte surfacique. Le rapport distingue les
cellules de la grille logique préparée de celles effectivement conservées.
Cela ne constitue pas une reprise locale préservant un ancien maillage.

Les contrôles directs refusent les demandes qu'ils ne peuvent appliquer :
réparation de la géométrie, reprise locale stricte, tailles par entité,
champs sphériques de raffinement ou correspondances périodiques. Le refus évite de présenter un réglage ignoré comme une
contrainte satisfaite. Les zones critiques restent soumises aux contrôles
natifs du résultat. Certains points de couture CAO peuvent ne pas être
échantillonnés par la grille ; une zone ponctuelle sur un tel point est
refusée explicitement. Les paramètres de lissage et d'optimisation Gmsh
ne sont pas appliqués à ces grilles directes ; ce statut figure dans le rapport.

## Divisions et tailles non uniformes

`mesh.structured.divisions` contient `[nx, ny, nz]`, avec des entiers de 1 à
2 000. Une surface ouverte utilise `nz=1`. Sans divisions explicites, le
noyau les déduit de la taille cible, puis vérifie les limites de calcul.
Le budget `max_elements` reste applicable avant la construction. La taille
cible est une aide au choix des divisions, pas une garantie que chaque arête
égale cette valeur ; les divisions explicites et la progression déterminent
la répartition réelle des cellules.

Pour une O-grid, les directions représentent respectivement le nombre de
couches radiales, le nombre de divisions par côté et les couches dans
l'épaisseur. Ce ne sont donc pas des directions cartésiennes X/Y/Z.

`mesh.structured.grading` contient trois rapports strictement positifs entre
largeurs d'intervalles consécutifs. `[1, 1, 1]` donne une répartition uniforme.
Un rapport supérieur à 1 fait croître les intervalles dans la direction
paramétrique positive ; un rapport inférieur à 1 les fait décroître. Une
progression trop forte est refusée si des intervalles ne sont plus
représentables distinctement en précision flottante.

Cette progression fournit un maillage non uniforme compatible avec la grille
structurée. Les champs métriques anisotropes et le raffinement local conforme
de blocs structurés ne sont pas encore fournis.

## Variantes quadratiques et formats

| Famille | `serendipity`, par défaut | `complete` |
| --- | ---: | ---: |
| Quadrilatère | 8 nœuds | 9 nœuds |
| Hexaèdre | 20 nœuds | 27 nœuds |
| Prisme triangulaire | 15 nœuds | 18 nœuds |
| Pyramide | 13 nœuds | 14 nœuds |

Une source STL ou discrète représente une géométrie facettisée. Dans ce
parcours, les nœuds quadratiques restent au milieu des arêtes droites de
cette géométrie ; le résultat conserve bien ses éléments d'ordre 2.
L'optimiseur de courbure destiné à la CAO analytique n'est pas appliqué à
ces sources. Le rapport indique `piecewise_linear_discrete` pour ce parcours. Une source
facettisée ne recrée pas automatiquement ses courbes CAO d'origine.

Les sphères primitives, en surface ou en volume, disposent d'un traitement
spécifique : chaque nœud quadratique d'arête de la frontière triangulée est
recalculé à partir des deux sommets de cette arête, puis projeté radialement
sur la sphère analytique, y compris après mise à l'échelle, rotation et
translation. Dans un volume TET10, les nœuds d'arêtes intérieures restent
aux milieux de leurs deux sommets ; ils ne sont pas rabattus sur la sphère.

Le rapport décrit cette méthode sous `analytic_sphere_edge_midpoint_projection`,
avec le nombre de nœuds projetés, l'erreur géométrique et les minima de
qualité et de Jacobienne des facettes. Les contrôles volumiques et de
frontière restent tous deux requis. Cette méthode ciblée ne constitue pas
un traitement universel des singularités de toute surface CAO.

La connectivité est conservée : un export incompatible doit échouer, sans
supprimer les nœuds supplémentaires. En particulier, les convertisseurs
INP et APDL actuels acceptent les quadrilatères à 8 nœuds et les hexaèdres
à 20 nœuds, mais pas toutes leurs variantes complètes. Les pyramides
quadratiques ne sont pas acceptées par ces convertisseurs. Le support
précis est décrit dans [FORMATS.md](FORMATS.md).

Le choix d'un format de fichier ne remplace pas la formulation physique
attendue par le solveur. Les vérifications de fichiers et les essais d'une
analyse complète dans le logiciel cible sont deux validations distinctes.

## API pour scripts et relances de maillage

Le [guide de l'API et du couplage solveurs](API_ET_COUPLAGE_SOLVEURS.md)
décrit `atelier.api.MeshingClient` et le protocole JSON natif versionné.
Le client expose la génération, l'examen, l'apprentissage, l'export, la
reprise locale quand elle est compatible, les capacités du noyau et la
planification d'une adaptation.

Les exemples exécutables sont :

```sh
python examples/api_generate.py examples/structured_hexa_api.json -o output/api
python examples/api_solver_loop.py --demo -o output/solver_demo
python -m atelier capabilities
```

L'exemple de boucle emploie un retour de solveur fictif explicitement
identifié. Il démontre les échanges de fichiers et le pilotage, sans
annoncer un calcul Code_Aster, Abaqus, ANSYS, CalculiX ou Cast3M exécuté.

Un adaptateur fournit l'indicateur choisi, ses régions et son identité de
maillage. Le noyau Rust vérifie les unités, les valeurs et l'empreinte,
puis décide des réglages à transmettre au prochain maillage. Le schéma
`atelier.solver.indicators.v1` est indépendant du nom du solveur.

La boucle génère et exporte le maillage pour le logiciel qui réalisera
réellement le calcul. L'adaptateur appelle ce logiciel, lit ses résultats
et retourne les indicateurs. Chaque interface de solveur doit être écrite
et vérifiée avec sa version et ses formulations ; ce protocole générique
ne constitue pas une bibliothèque de connecteurs déjà validés.

Pour les blocs directs, l'adaptation régénère globalement une grille plus
fine de la même famille. Elle ne crée pas des raccords non conformes par
insertion silencieuse d'un patch local. Le parcours non structuré peut
employer des champs de raffinement locaux. Les rapports distinguent ces
stratégies et les arrêts par convergence, absence de progrès ou budget.

La poursuite d'un calcul non linéaire ou dynamique impose au solveur de
transférer les champs et son état interne : déplacements, températures,
variables aux points d'intégration, historique du matériau ou du contact,
selon le problème. Le mailleur ne réalise pas implicitement ce transfert.
Les callbacks externes gèrent leurs propres délais et leur annulation ;
le client vérifie son budget entre les appels et borne les processus natifs.

## Ce qui change pour l'apprentissage

La mémoire conserve la famille, le layout quadratique, les divisions et la
progression demandés. Les candidats de recherche doivent respecter ces
choix ; une recette quadrilatère ne devient pas une recette triangulaire
pour améliorer artificiellement son taux de réussite.

La représentation v3 garde 128 entrées, réparties entre un résumé global,
une description spatiale à deux échelles, les zones et tailles, la
topologie et les paramètres des actions structurées. Il s'agit encore d'un
petit réseau sur des caractéristiques construites, pas d'un réseau de
graphes apprenant directement la CAO.

Les anciennes caractéristiques reconstructibles sont réencodées depuis
les projets et catalogues sauvegardés. Les données brutes restent
conservées ; les poids incompatibles sont réinitialisés explicitement.
Un ancien corpus dont le schéma n'est pas compatible est refusé, sans
réécriture silencieuse de ses segments.

La recherche exploite les résultats déjà observés pour réordonner les
prochains essais, avec une exploration prévue tous les quatre essais.
L'estimateur séquentiel fournit un proxy d'incertitude non calibré ; il ne
prétend pas mesurer une probabilité d'erreur physique. Cette règle est
distincte de la mise à jour du réseau, qui intervient à la fin de la recherche.

La validation du réseau de mémoire exige une amélioration de l'erreur de
prédiction et du choix au budget d'un candidat, sans régression aux budgets
comparés de un, deux et quatre candidats. Ce contrôle porte sur des actions
archivées de contextes réservés : ce ne sont pas de nouveaux calculs ni une
comparaison de temps réel. Les contrôles géométriques restent l'autorité
pour accepter un maillage.

Dans l’archive rc6, le profil de corpus `structured_direct` proposait huit familles orientées
vers les nouvelles grilles : quadrilatères et hexaèdres simples ou percés,
prismes, pyramides, volumes minces et grilles à progression de tailles.
Le profil `general` comptait 24 familles. Le [profil actuel](CORPUS_ET_LANGAGES.md) utilise exclusivement des quadrilatères et des hexaèdres.
Planifier 250 000 configurations structurées et 750 000 aléatoires ne
signifie toujours pas les avoir calculées. Les résultats réellement
exécutés doivent être lus dans les rapports de livraison.

## Pilote historique de l’archive rc6

Le rapport du corpus structuré (`verification/rc6/structured_corpus/summary.json`)
consigne 32 configurations réellement évaluées : 16 structurées et 16
aléatoires, sur huit familles. Les 32 cas ont produit un maillage et les
96 stratégies ont respecté les critères définis pour ce pilote. Les formes
se répartissent en 12 cas quadrilatères, 12 hexaédriques, quatre prismatiques
et quatre pyramidaux. Ces variantes paramétriques simples ne représentent
pas un essai sur des CAO industrielles arbitraires.

La reprise a été exécutée dans une nouvelle session. L'entraînement de trois
passages, par mini-lots de 16, utilise 20 cas d'entraînement, huit de
validation et quatre de test, séparés par famille. Le conseiller 128→32→1
reste désactivé pour le classement : son gain de sélection en validation
vaut environ −0,008215. Une chaîne d'apprentissage fonctionnelle ne démontre
pas à elle seule un avantage du réseau sur les choix classiques.

Le plan d'un million du même profil contient zéro cas calculé et zéro
évaluation de moteur. Les tests de protocole, fixtures synthétiques et
relectures de fichiers ne sont pas ajoutés aux 32 cas de ce pilote.

## Statique, dynamique et thermique

Ces calculs peuvent utiliser les mêmes familles de mailles. Ce qui change
est la résolution nécessaire pour les phénomènes étudiés. Un calcul
statique doit résoudre les gradients de la solution ; la propagation
d'ondes impose aussi de résoudre les longueurs d'onde pertinentes ; un
calcul thermique doit représenter ses gradients et les évolutions
spatiales de température. Un calcul explicite peut en outre être pénalisé
par les plus petits éléments à travers sa condition de stabilité temporelle.

Le mailleur ne change pas automatiquement la forme des éléments suivant
le texte du champ `physics`. Le solveur et son adaptateur déterminent le
sens de l'indicateur et de la tolérance. Le but est de conserver les formes
choisies tout en adaptant la discrétisation au calcul réel.

## Validation et plateformes

Le rapport global du 19 novembre 2026 (`verification/native_release/summary.json`)
consigne **195 tests Python et 139 tests Rust réussis**, sans test ignoré,
sous Linux. Les huit exemples de livraison ont été maillés à l'ordre 2 et
leurs paquets d'export ont été produits. Les tests incluent les variantes
directes, les zones critiques 0D/1D/2D, les frontières courbes, les sphères
surfaciques et volumiques, et les sources discrètes quadratiques.

Le bilan des réseaux doit être lu séparément :

| Conseiller | Résultat de cette livraison |
| --- | --- |
| Historique, 8→12→3 | Activé dans son domaine classique ; 10 cas d'entraînement, six de validation, gain d'environ 0,02046 |
| Mémoire interactive v3, 128→12→1 | Classement inactif ; six géométries, 29 observations ; gain de choix au budget d'un candidat égal à zéro |
| Corpus direct, 128→32→1 | Classement inactif ; 32 cas, gain de sélection en validation d'environ −0,008215 |
| Corpus général, 128→32→1 | Classement inactif ; pilote de 48 cas, sans gain de sélection validé |

Le résultat positif du guide historique concerne son jeu et son domaine
classiques. Il n'est pas attribué aux nouveaux maillages structurés et ne
prouve pas une généralisation industrielle. La recherche et la mémoire
exacte revalidée restent utilisables lorsque le classement neuronal est inactif.

Le contrôle de protocole API (`verification/api/rc6_protocol_demo.json`)
consigne la CLI des capacités, la génération de 24 hexaèdres quadratiques,
une boucle à deux évaluations de solveur fictif et la planification native
d'une adaptation. Il ne comporte aucun calcul physique externe.

Les preuves rc6 sont distinctes des mesures de performance rc5. Les
anciens temps d'entraînement, de stockage et d'affichage restent des
mesures historiques de leurs fixtures ; ils ne représentent pas de nouveaux
benchmarks du générateur direct.

Les sources et lanceurs ciblent macOS, Linux et Windows. Une compilation
sur Linux ne constitue pas un essai sur un MacBook Pro M3. Les machines et
versions effectivement vérifiées doivent être identifiées dans les
rapports, ainsi que les tests exécutés dans les solveurs externes.

L'ordre 2, les limites d'éléments et les nombres de divisions déterminent
le coût et la mémoire de la grille. Aucun réglage ne garantit un temps
minimal sur tous les ordinateurs. La subdivision automatique en blocs de
toute CAO défectueuse, l'adaptation anisotrope et la reprise d'état physique
universelle restent hors du périmètre livré.

Le contrôle après extraction de l’archive (`verification/rc6/archive_smoke.json`) vérifie aussi le noyau embarqué, la génération HEX20 avec export INP depuis un script, la boucle de protocole solveur et le plan de corpus. Ce contrôle utilise les dépendances installées sous Linux ; il ne constitue pas un essai sur Mac ni dans un solveur physique.
