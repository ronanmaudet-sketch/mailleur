# Périmètre et limites de la version candidate

## Géométries et défauts

Le moteur de maillage et de CAO est Gmsh 4.15.2 avec OpenCASCADE. Les entrées
CAO prises en charge sont STEP/STP, IGES/IGS et BREP. Le STL représente une
surface triangulée : il ne recrée pas à lui seul une CAO exacte ni un volume
fermé exploitable dans tous les cas.

Les opérations de réparation sont demandées explicitement, avec une tolérance
exprimée dans l'unité du modèle. Elles peuvent notamment recoudre des surfaces
proches et traiter certains petits défauts. Elles ne déterminent pas
automatiquement si une ouverture est un trou fonctionnel ou une face manquante.
Une tolérance trop grande peut modifier un détail utile ; le rapport doit être
examiné après réparation.

Des surfaces qui s'intersectent, des faces superposées, des régions
non-manifold, des trous importants ou une topologie incohérente peuvent exiger
une correction dans l'outil CAO d'origine. Le logiciel doit signaler l'échec
plutôt que produire une fermeture arbitraire. La présence d'un bouton de
réparation ne constitue pas une garantie sur tous les défauts géométriques.

Le générateur de plaque fournit des congés et chanfreins de contour. Il ne
propose pas un modeleur CAO général avec congés 3D interactifs. Les modèles
plus complexes sont importés à partir d'une CAO déjà préparée.

## Types d'éléments et qualité

Le parcours classique génère des triangles et des tétraèdres. Les nouveaux
blocs paramétriques compatibles permettent la génération directe de
quadrilatères, hexaèdres, prismes et pyramides aux ordres 1 ou 2. La
recombinaison est refusée. Le [guide rc6](EVOLUTIONS_RC6.md) précise les
primitives acceptées et les contrôles incompatibles. La décomposition
automatique de volumes CAO arbitraires en blocs hexaédriques reste à développer.

Les éléments d'ordre 2 sont conservés seulement dans les formats et types
effectivement pris en charge par les convertisseurs. Une conversion qui ne
peut pas représenter un élément doit échouer clairement plutôt que supprimer
silencieusement ses nœuds intermédiaires.

La taille de maille est un réglage du moteur, pas une borne universelle sur
toutes les dimensions d'éléments CAO. La courbure, les petits détails et les
transitions peuvent demander un maillage plus fin. Les limites de temps et
d'éléments peuvent interrompre le calcul ; elles ne garantissent pas une borne
stricte de mémoire pendant toutes les phases internes du moteur.

Des Jacobiennes positives et des indicateurs de forme satisfaisants ne suffisent
pas à établir la précision d'un calcul mécanique, thermique ou multiphysique.
Les études de convergence, métriques du solveur et contrôles propres au problème
restent nécessaires. La visualisation utilise les sommets principaux et peut
échantillonner les très gros maillages : elle ne remplace pas le contrôle du
fichier complet et de son rapport.

## Exports

Un maillage exporté n'est pas un dossier complet de calcul : matériaux,
conditions aux limites, contacts, unités et formulations sont à définir dans
le solveur. Les correspondances de groupes sont décrites dans le manifeste
d'export et dans `FORMATS.md`.

Le format INP demande une interprétation cohérente des surfaces et des pièces
planes. L'export APDL construit des MESH200 sans degrés de liberté ; leur
conversion ou affectation vers les éléments physiques d'ANSYS est une étape
distincte. Le script Cast3M s'appuie sur un import UNV et doit être utilisé
avec une version de Cast3M prenant en charge ce flux.

Les tests de relecture des fichiers prouvent certains invariants de conversion.
Ils ne prouvent pas qu'une analyse complète a été exécutée dans chaque version
de chacun des solveurs nommés. Les essais dans les solveurs cibles doivent
rester séparés et clairement identifiés.

## Apprentissage et zones critiques

La recherche sur la pièce courante utilise un ensemble borné de recettes de
maillage existantes. Elle ne constitue pas un apprentissage par renforcement
du placement de chaque nœud et ne crée pas une décomposition arbitraire en
blocs hexaédriques. Les variantes réellement essayées figurent dans le rapport.
Une meilleure recette parmi les essais effectués n'est pas une preuve du
meilleur maillage possible.

Une pièce importée peut entrer dans ce parcours, sous réserve que le backend
puisse l'interpréter et la mailler. Mémoriser un échec ne répare pas la source.
Les défauts arbitraires, intersections et pertes de topologie restent soumis
aux limites du moteur géométrique.

La mémoire des expériences réutilise des recettes dans un contexte identifié ;
elle ne prouve pas une généralisation à des géométries différentes. Le réseau
est actualisé sur l'historique et peut ordonner l'exploration. Les contrôles
d'admissibilité restent obligatoires pour chaque maillage, même si une recette
avait réussi auparavant. Un historique peu varié ne suffit pas à démontrer un
gain sur des pièces industrielles nouvelles.

Les zones critiques sont définies par des entités géométriques, une marge et un
seuil. Les entités de dimension inférieure sont associées aux éléments par
adjacence de nœuds. Une marge positive utilise une boîte englobante élargie :
cette méthode conservatrice peut couvrir plus large qu'une distance CAO exacte.
Le marquage « contact », « interface » ou « contrainte » exprime une
priorité de qualité ; il ne crée pas un contact physique, un chargement, une
loi matériau ou un calcul mécanique. La continuité demandée est à définir avec
les correspondances ou la topologie appropriées. Une zone critique ne recoud
pas à elle seule deux pièces séparées.

Les contraintes et les erreurs de solution sont calculées par le solveur
externe. Les régions sensibles peuvent être indiquées par l'utilisateur ou
transmises par un adaptateur suivant le protocole de l'[API](API_ET_COUPLAGE_SOLVEURS.md).
La boucle générique pilote génération, export, calcul externe et adaptation ;
chaque connecteur doit encore être validé avec son logiciel et sa version.
Le transfert des champs et des états internes reste une responsabilité du
solveur, particulièrement en dynamique et en non-linéaire. L'adaptation des
blocs directs est une régénération globale ; leur reprise locale stricte
et leur adaptation anisotrope ne sont pas fournies.

Les tailles locales sont des cibles isotropes. Il n'y a pas de champ métrique
anisotrope ni de garantie que chaque arête égale exactement la taille demandée.
Une grande valeur locale n'annule pas une contrainte globale plus fine. Les
frontières partagées et l'extension des tailles peuvent raffiner des régions
voisines. En volume, les champs de taille demandent un générateur compatible ;
la recherche doit privilégier Delaunay lorsque ces contrôles sont présents.

L'index de mémoire est borné à 1 000 contextes, 32 000 observations et 128 Mio.
Le journal d'essais est borné à 512 Mio. Les limites provoquent un arrêt
explicite sans suppression automatique. Les sources importées et les fichiers
d'essai occupent également de l'espace.
Cette mémoire locale n'est pas un service de stockage illimité et ne constitue
pas une sauvegarde externe des projets. Le modèle de mémoire
conserve 1 561 paramètres avec la représentation rc6 v3. Il peut rester
désactivé pour le classement faute de gain validé ; cela n'empêche pas le
réemploi exact revalidé d'une recette.

Le conseiller historique sur corpus synthétique reste un module distinct.
Sa comparaison sur familles réservées n'est pas transférable au réseau de
mémoire et ne prouve pas l'efficacité de celui-ci. Les résultats de la livraison
identifient séparément ces expériences.

Le corpus de grande taille utilise un stockage segmenté séparé. Planifier
250 000 cas structurés et 750 000 cas aléatoires ne signifie pas que ces cas
ont été calculés. Les mesures de livraison portent sur les lots réellement
exécutés, et les essais de chargement ou d'entraînement sur des lignes JSONL
synthétiques ne sont pas des calculs CAO ni des maillages supplémentaires.
Les empreintes spatiales de la représentation restent des résumés ; elles ne
garantissent pas de distinguer toutes les géométries industrielles possibles.

## Distribution et systèmes

Le livrable est un projet source installable en Python, avec des lanceurs pour
macOS, Windows et Linux. Il ne comprend pas d'application macOS signée/notariée,
d'installateur Windows natif ou de certification pour une distribution Linux.
Les dépendances sont téléchargées lors de la première installation.

Les tests préparés pour d'autres systèmes ne sont pas présentés comme des tests
déjà exécutés. Le rapport de livraison distingue l'environnement réellement
utilisé de la matrice CI proposée.

## Sources techniques

- Gmsh, API, noyau OpenCASCADE et limites : https://gmsh.info/doc/texinfo/gmsh.html
- Formats meshio : https://github.com/nschloe/meshio
- Interfaces solveurs et groupes : voir les sources précises de `FORMATS.md`.
