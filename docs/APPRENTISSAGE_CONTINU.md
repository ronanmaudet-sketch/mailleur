# Apprendre sur une géométrie nouvelle

La recherche de recettes porte sur la **pièce réellement ouverte**.
Elle accepte les primitives natives et les sources importées que le backend
peut interpréter. Une pièce n'a pas besoin de figurer dans le corpus synthétique
historique pour entrer dans cette recherche.

Cette fonction combine trois mécanismes distincts :

| Mécanisme | Ce qu'il conserve ou fait |
| --- | --- |
| Recherche | Produit et compare plusieurs vrais maillages dans un budget d'essais |
| Mémoire d'expériences | Retient le contexte, les recettes, réussites, échecs et mesures |
| Réseau de mémoire | Apprend sur l'historique pour prédire l'intérêt des recettes et, si la validation le permet, les explorer dans un ordre plus utile |

Le réseau n'émet pas lui-même chaque nœud ou chaque élément. Les algorithmes
Gmsh restent responsables de leur construction, pilotés par Rust.

## Parcours conseillé

1. Préparer la pièce sur la page Géométrie. Confirmer les unités et le mode
   plan, surface ou volume. Examiner les entités réellement présentes.
2. Sur la page Maillage, régler la taille globale, la taille minimale, l'ordre,
   les tailles locales et les correspondances nécessaires.
3. Marquer les zones à protéger : contact, interface, contrainte ou autre.
   Leur donner un nom, les tags d'entités, une marge et une qualité minimale.
4. Définir les exigences globales de qualité et le budget d'essais, puis lancer
   **Apprendre sur cette pièce**. Le calcul reste dans un processus séparé de
   l'interface ; le budget est un nombre maximal d'essais, pas un résultat garanti.
5. Lire le rapport : essais, erreurs, recette choisie, qualité globale et par
   zone, éventuel réemploi de mémoire et état du modèle.
6. Exporter uniquement le résultat accepté. Si aucun essai n'est admissible,
   examiner le diagnostic, la géométrie, les marges et les réglages avant de
   lancer une nouvelle recherche.

Les réglages et zones sont conservés dans le projet portable. La mémoire de
l'atelier est un historique distinct ; transporter un projet ne doit pas être
interprété comme transporter automatiquement l'ensemble de cet historique.

## Ce qui est essayé

Une recette décrit notamment l'algorithme et la taille cible, avec des variantes
de raffinement local. La recherche peut modifier ces paramètres et concentrer
le raffinement sur les entités protégées ou celles identifiées comme faibles
par les mesures précédentes. Les tailles minimales et la limite d'éléments
restent applicables.

Les choix d'algorithme proposés sont Automatique, Delaunay et Frontal selon leur
compatibilité avec le mode et les contraintes. En volume, Automatique et
Delaunay utilisent ici le même générateur. Les champs locaux ne sont pas
compatibles avec tous les générateurs 3D ; leur présence doit être prise en
compte dans les recettes.

Une recherche de 12 essais ne signifie donc ni 12 algorithmes indépendants,
ni 12 géométries différentes. Elle n'explore pas toutes les topologies ou toutes
les partitions possibles d'une pièce. Il n'y a pas de décomposition générale en
blocs structurés et pas de garantie de volume tout-hexa.

## Qualité globale et régions protégées

La qualité est mesurée sur les **éléments réellement calculés**, indépendamment
de la réduction éventuelle de l'aperçu graphique. Les éléments détectés comme
inversés ou dégénérés restent refusés, même pendant la recherche.

La politique de qualité distingue :

- le minimum acceptable pour chaque élément ;
- le seuil sous lequel un élément est compté comme médiocre ;
- le nombre ou la fraction maximale d'éléments médiocres, si ces limites sont
  définies ;
- le minimum plus strict demandé dans chaque zone critique.

Les recettes sont d'abord comparées sur leur admissibilité et les violations
des régions protégées, puis sur le nombre et la sévérité des éléments médiocres
et la qualité minimale. Une bonne moyenne ne doit pas masquer un élément
insuffisant dans un contact ou un raccord protégé. Un score prédit par le réseau
n'est jamais une autorisation d'accepter un résultat qui viole ces règles.

Une zone désigne des entités de dimension 0 jusqu'à celle du maillage, avec
leurs tags. Si les dimensions sont identiques, tous les éléments de l'entité
sont concernés. Sinon, les éléments de dimension supérieure partageant un
nœud avec l'entité ou sa fermeture sont protégés. Un tag absent ou une zone
sans élément adjacent provoque un refus.

Une marge positive ajoute les éléments rencontrant la boîte englobante de
l'entité, agrandie de cette distance. Cette sélection est **conservatrice** :
elle peut protéger plus large qu'une vraie distance à la face ou à la courbe.
Elle ne représente pas un décalage CAO exact. Le rapport précise cette méthode.
Vérifier le nombre d'éléments concernés et leur qualité dans le rapport.
En volume, une zone définie sur une face contrôle aussi les facettes de cette
face directement, en plus des éléments volumiques adjacents : la qualité d'un
tétraèdre ne suffit pas à garantir celle de sa facette de contact. Les champs
`boundary_elements`, `boundary_minimum` et `boundary_below_minimum` détaillent
ce contrôle. Une violation de l'un ou l'autre contrôle fait refuser la zone.

Une zone
nommée « contact » ne définit pas la loi physique de contact et une zone nommée
« interface » ne crée pas à elle seule la continuité entre deux pièces.

Les seuils sont des exigences de forme d'éléments. Ils ne remplacent pas une
étude de convergence du calcul mécanique et ne déterminent pas la précision
physique à eux seuls. Un élément volontairement allongé peut aussi répondre à
un besoin de modélisation ; une métrique doit toujours être interprétée avec
le type d'élément et le problème étudié.

## Comment indiquer les fortes contraintes

Le type « contrainte » signifie **région à protéger fournie par l'utilisateur**.
Le mailleur ne déduit pas les champs de contraintes à partir de la seule CAO.
Il ne connaît pas, sans données supplémentaires, les charges, matériaux,
conditions aux limites et états de contact qui produiraient ces contraintes.

Une première utilisation consiste à marquer les régions sensibles connues.
Les résultats d'un calcul effectué séparément peuvent aider à choisir ces
régions. La rc3 ne fournit pas de boucle automatique solveur–estimation
d'erreur–adaptation du maillage, ni de chargement automatique d'un champ de
contraintes servant de critère à cette boucle.

## Taille non uniforme

Les tailles globales, locales et la courbure peuvent produire un maillage de
densité variable. Les tailles sont exprimées dans l'unité d'entrée, puis
converties dans le noyau. Ce sont des cibles isotropes, pas un champ métrique
anisotrope ni une longueur exacte imposée à chaque arête.

La plus petite contrainte applicable est retenue. Une taille locale supérieure
à la taille globale ne grossit donc pas cette région. Une taille locale
inférieure à la taille minimale est refusée. Les frontières partagées et la
propagation des tailles peuvent également raffiner une région voisine.

Le parcours d'apprentissage de la pièce courante inclut ces réglages dans son
contexte. Cela le distingue du guide historique, qui reste hors domaine lorsque
des tailles locales, correspondances ou sélections partielles sont présentes.

## Mémoire et répétition d'une pièce

L'identité d'une expérience est calculée à partir du contenu de la géométrie
et de son contexte. Modifier le nom du projet ne doit pas créer artificiellement
une nouvelle géométrie. À l'inverse, remplacer le contenu d'un fichier portant
le même nom ne doit pas déclencher une réutilisation aveugle.

Le contexte comprend les paramètres influençant le maillage, notamment les
unités, le mode, les tailles, la qualité et les zones protégées. Si le besoin
mécanique change, ses zones de priorité doivent être mises à jour : le
programme ne découvre pas un changement de chargement absent du projet.

Une recette retrouvée est **exécutée de nouveau**. Le maillage obtenu est
contrôlé sur le contexte courant avant d'être retenu. « Réutilisé » signifie
réemploi d'une expérience comme point de départ, pas acceptation d'un résultat
sans contrôle.

Le dossier mémoire par défaut est `experience/` dans le dossier de travail
de l'atelier. Il contient notamment :

| Fichier ou dossier | Rôle |
| --- | --- |
| `events.jsonl` | Journal des essais successifs, y compris les échecs |
| `memory.json` | Index des contextes et du dernier état observé pour chaque recette |
| `sources/` | Copies des sources importées identifiées par empreinte |
| `model.json` | Version courante du modèle de mémoire |
| `models/model-000001.json`, etc. | Versions successives du modèle |

Le journal et l'index ont des rôles différents : le journal conserve chaque
essai, y compris les échecs et les réemplois. L'index garde le dernier état par
recette et fournit une observation représentative de chaque recette au replay ;
répéter cent fois une recette ne crée donc pas cent exemples indépendants.

L'index est borné à **1 000 contextes**, **32 000 observations** et **128 Mio** ;
le journal est borné à **512 Mio**. Atteindre une limite provoque un arrêt
explicite, sans purge automatique de l'historique. Ces limites empêchent de
traiter la mémoire comme une base d'expériences illimitée. Les rapports
indiquent l'historique conservé et les données utilisées par le réseau.

Le journal est synchronisé avant l'index et les sauvegardes d'index sont
atomiques. La mémoire est verrouillée pendant l'opération ; le verrou du
système est libéré si le processus est interrompu. Ce mécanisme permet la
récupération des essais journalisés après une annulation, sans assimiler un
essai interrompu à un maillage accepté.

Ces données restent locales. Conserver le dossier mémoire avec les projets
permet de garder cet historique. Les sources et essais peuvent occuper de
l'espace ; une copie d'archive est préférable avant toute gestion manuelle de
ce dossier.

## Actualisation du réseau et validation

Le réseau de mémoire rc4 possède **128 entrées, 12 neurones cachés et une sortie**,
soit **1 561 paramètres ajustables**. Sa sortie estime l'intérêt d'une recette.
Il est distinct du conseiller historique à **8 entrées, 12 neurones cachés et
3 sorties**, soit **147 paramètres**, qui prédit les scores des trois choix
d'algorithme.

Les nouvelles entrées comprennent les paramètres globaux et des empreintes
spatiales des entités, des frontières et des zones critiques, ainsi que des
informations topologiques. Ces résumés ne sont pas une description CAO exacte
ni un apprentissage direct du placement des nœuds. Les fichiers du réseau
rc3 à 16 entrées ne sont pas des poids compatibles avec cette architecture.
Le rapport doit indiquer le traitement des anciennes observations et toute
réinitialisation explicite d'un réseau incompatible ; l'historique ne doit
pas être présenté comme entraîné avec des entrées qui n'existaient pas.

Lorsqu'un ancien cas dispose de son projet, de sa recette et des informations
géométriques suffisantes, ses nouvelles caractéristiques sont reconstruites.
Les 16 valeurs originales restent conservées sous `legacy_features`. Une
ancienne CAO importée sans catalogue utilisable reste conservée dans
l'historique, mais exclue de ce nouveau replay avec une raison explicite.
Les compteurs `representation_migration` distinguent ces situations. Une
relation topologique absente d'un ancien catalogue est signalée comme inconnue,
pas inventée pour compléter le vecteur.

Le [pipeline de corpus segmenté](CORPUS_ET_LANGAGES.md) est distinct de cette
mémoire interactive. Il permet de planifier une campagne beaucoup plus grande
et de former des mini-lots sans étendre artificiellement les limites de
`memory.json`.

Le réseau de mémoire est chargé lorsqu'une version compatible existe, puis
actualisé avec les observations de l'historique. Ce replay évite d'entraîner
uniquement sur la dernière pièce. Il n'est pas une preuve d'absence absolue
d'oubli ; les versions et mesures permettent de suivre les changements.

L'utilisation du modèle pour classer les recettes dépend d'une validation
séparant les géométries. Avec trop peu de géométries distinctes ou sans avantage
démontré, cette utilisation reste désactivée. La recherche classique et la
mémoire exacte restent utiles même dans ce cas. La qualité admissible est
toujours contrôlée sur les maillages réellement produits.

Une réussite sur une géométrie déjà explorée prouve au plus la reproduction
d'une recette dans ce contexte. Pour revendiquer un gain de généralisation,
il faut des pièces nouvelles réservées à l'évaluation, représentatives du
travail prévu et absentes de l'entraînement. Ajouter toutes les pièces évaluées
à l'historique puis les présenter comme une évaluation indépendante serait
incorrect.

## Commandes et paramètres JSON

Depuis l'environnement installé, la commande `atelier-mailleur` expose :

```sh
atelier-mailleur learn examples/apprentissage_plaque_zones.json -o resultat_plaque --max-trials 12 --epochs 100 --max-seconds 240
atelier-mailleur learn projet_importe.json --source piece.step -o resultat_import --memory memoire_partagee
atelier-mailleur memory --workspace "$HOME/AtelierMailleur"
```

Sous Windows, adapter le chemin du dossier personnel. `--no-memory` permet de
lancer la recherche sans réemploi de mémoire ; les paramètres exacts et les
essais restent décrits dans le rapport de l'opération. Le budget en secondes
s'ajoute au budget en essais. Il est vérifié entre les essais ; un essai en
cours peut donc dépasser ce budget, sous réserve du délai maximal du processus
appliqué par l'interface. Une interruption peut laisser moins d'essais exécutés
que demandé.

Le projet peut conserver les réglages suivants :

```json
{
  "quality_policy": {
    "minimum_quality": 0.05,
    "poor_threshold": 0.2,
    "max_poor_fraction": 0.02,
    "strict": true
  },
  "critical_zones": [
    {
      "name": "Face de contact",
      "kind": "contact",
      "dimension": 2,
      "tags": [1],
      "buffer": 0.3,
      "minimum_quality": 0.35
    }
  ],
  "learning": {
    "max_trials": 12,
    "epochs": 100,
    "use_memory": true,
    "max_seconds": 240
  }
}
```

Les tags ne sont pas interchangeables entre pièces : utiliser le catalogue
réel de la géométrie. La marge est exprimée dans l'unité d'entrée. La qualité
est sans dimension, sur l'intervalle de la métrique utilisée. Un seuil de 0,35
est un exemple de réglage, pas une certification universelle.

`max_poor_elements` peut compléter ou remplacer `max_poor_fraction`. Si les
deux sont fournis, les deux limites s'appliquent. Le mode strict doit rester
activé pour produire un résultat destiné à l'export. Les essais internes
utilisent un mode de diagnostic qui peut écrire un maillage non accepté ;
ce fichier d'essai ne doit pas être exporté comme résultat valide.

Un raffinement local sphérique peut être enregistré dans
`refinement_regions` sous la forme
`{"center": [3, 3, 0], "radius": 1.4, "size": 0.22}`. Centre, rayon et taille
sont dans l'unité d'entrée. Ce champ cible une région spatiale ; il n'identifie
pas une zone de fortes contraintes et ne change pas ses critères de qualité.
La transition du champ est également soumise aux autres contraintes de taille.

## Rapport et distinction avec le corpus historique

Le résultat contient `report.experience` avec les identités, le nombre d'essais,
la recette choisie, l'éventuel réemploi et l'état d'apprentissage. La liste
`trials` distingue génération réussie, qualité acceptée et échec. Une génération
réussie n'implique pas automatiquement une qualité suffisante.

Les métriques `report.quality` comprennent le minimum et la moyenne, ainsi que
`poor_count`, `poor_fraction`, `poor_severity`, l'acceptation et le détail des
zones critiques. Le rapport décrit le résultat ; l'aperçu seul ne fait pas foi. L'inspection de
la géométrie est un diagnostic d'affichage (`diagnostic_only`) : elle peut
rester disponible malgré un ancien tag de zone devenu invalide, et ne valide
pas pour autant les critères du futur maillage.

Le bouton de corpus historique continue d'évaluer ses exemples synthétiques et
son conseiller d'algorithmes. Ses 16 configurations par défaut ne représentent
pas le nombre d'expériences de la mémoire continue. Les nombres pertinents sont
ceux du rapport de l'opération réellement exécutée. Avec une ou deux géométries
seulement, le classement appris peut rester désactivé faute de validation
suffisante, alors que la mémoire des recettes fonctionne déjà.
