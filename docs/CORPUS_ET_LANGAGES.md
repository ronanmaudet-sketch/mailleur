# Corpus à grande échelle et répartition Python / Rust

La rc4 ajoute un pipeline distinct de la mémoire interactive d'une pièce.
Son objectif par défaut est **250 000 configurations structurées et 750 000
configurations aléatoires**, soit un million de cas prévus. Une planification
n'exécute aucun calcul de maillage. Les essais de livraison portent sur de
petits lots réellement exécutés ; ils ne constituent pas un corpus d'un million
de maillages déjà calculés.

## Résultats réellement exécutés pour cette livraison

Le [rapport du corpus rc4 (archive complète)](provenance/PREUVES_EXTERNES.md) et le
[rapport natif](../verification/native_release/summary.json) consignent :

| Vérification | Résultat observé |
| --- | --- |
| Plan cible | 250 000 cas structurés + 750 000 aléatoires ; zéro cas calculé et zéro appel de maillage |
| Métadonnées de ce plan | 3 fichiers, 5 058 octets |
| Pilote réellement exécuté | 48 cas : 24 structurés, 24 aléatoires, 24 familles |
| Cas ayant produit un maillage | 46 ; les deux autres sont des défauts géométriques invalides attendus |
| Cas ayant une stratégie admissible | 45 |
| Stratégies évaluées ou explicitement refusées | 96 : 88 acceptées et 8 refusées |
| Appels du moteur | 140, dont 48 inspections de référence indépendantes des stratégies |
| Reprise | Premier lot de 24, puis 24 cas restants dans une nouvelle session Python |
| Caractéristiques | 128 entrées, schéma `atelier.geometry_action_features.v2` |
| Entraînement | 2 passages, mini-lots de 16 ; 32 cas d'entraînement, 8 de validation, 8 de test |
| Exemples de stratégies par partition | 64 d'entraînement, 16 de validation, 16 de test |
| Conseiller de corpus | 128→32→1, 4 161 paramètres, sauvegardé et installé dans l'atelier |
| Gain de sélection de validation | 0,0 ; activation du classement désactivée |
| Suites complètes | 140 tests Python et 93 tests Rust réussis, aucun test ignoré |

La qualité d'une stratégie, son exécution et l'admissibilité d'un cas ne sont
pas le même compteur. En particulier, deux stratégies d'un même cas invalide
restent deux résultats refusés, sans deux maillages fabriqués. Les 140 appels
incluent les inspections et ne sont donc pas assimilés à 140 maillages acceptés.

La suite vérifie aussi l'entraînement par lots sur 33 010 enregistrements JSONL
synthétiques. Ces données servent à tester le chargement et les mini-lots ;
elles ne représentent pas 33 010 calculs CAO ni des géométries supplémentaires.
Les 57 anciens tests du moteur Python ont été retirés explicitement et 16
tests de frontière et d'entrées/sorties les remplacent ; ce changement n'est
ni un succès ni un saut de test. Voir le [périmètre de validation](VALIDATION.md).

## Ce que signifie une configuration

Un cas associe une géométrie, son mode plan/surface/volume, ses unités et ses
objectifs de discrétisation. Plusieurs stratégies peuvent être calculées pour
ce même cas. Chaque stratégie produit un résultat mesuré ou un échec explicite.

Il faut donc distinguer :

- le nombre de cas planifiés ;
- le nombre de cas terminés et enregistrés ;
- le nombre de calculs de maillage effectivement exécutés ;
- le nombre d'exemples admis pour l'entraînement ;
- le nombre de passages d'entraînement sur ces exemples.

Les deux lots utilisent le moteur classique pour obtenir leurs résultats de
référence. Le réseau ne produit pas lui-même les étiquettes qui servent ensuite
à valider ses propres prédictions.

## Planifier, calculer, reprendre, entraîner

Depuis le dossier du projet et son environnement Python :

```sh
python -m atelier corpus plan --directory output/corpus \
  --structured 250000 --random 750000 --seed 42 --strategies 4 --shard-size 1000

python -m atelier corpus status --directory output/corpus

python -m atelier corpus run --directory output/corpus --limit 20 --max-seconds 240

python -m atelier corpus train --directory output/corpus \
  --epochs 3 --batch-size 128 --max-records 10000 --max-seconds 240
```

Le panneau de corpus de la page Maillage expose également ces opérations.
Un lot terminé ne déclenche pas automatiquement le lot suivant. Réexécuter
`run` reprend la progression ; les cas déjà enregistrés ne sont pas recalculés
comme s'ils étaient nouveaux. Le bilan distingue résultats acceptés et échecs.

`--max-records 10000` borne ici la partie du corpus lue pour cet entraînement.
`--max-records 0` demande tous les cas effectivement calculés, et non tous ceux
qui figurent seulement dans le plan. Un grand entraînement reste une opération
explicite dont le coût doit être mesuré sur les machines utilisées.

La graine et l'indice d'un cas déterminent sa génération. Les variantes
structurées et aléatoires sont réparties en familles ; cela permet de
reproduire un cas sans construire préalablement un million d'objets Python ou
de fichiers individuels. Le hasard ne garantit pas de couvrir toutes les
formes ni tous les défauts industriels possibles.

Le budget de temps est contrôlé entre traitements. Le délai global du
processus reste une limite distincte : un maillage isolé très long peut être
arrêté par l'interface. Les paramètres ne constituent pas une estimation de la
mémoire qui sera consommée par Gmsh.

## Stockage et apprentissage par lots

Le plan et sa progression sont séparés des résultats détaillés. Les cas sont
enregistrés dans des segments JSONL, avec une ligne par cas et les résultats
de ses stratégies. Le programme ne charge pas l'ensemble du corpus pour
planifier un cas ou pour former un mini-lot.

Le nombre d'enregistrements et de stratégies terminées est un résultat mesuré,
pas une conséquence du nombre indiqué dans le plan. En particulier, une
configuration géométriquement invalide ne devient pas un maillage valide
simplement parce qu'elle occupe une ligne du corpus.

Par défaut, les maillages complets d'une campagne ne sont pas tous conservés.
L'option `--retain-meshes` sert à conserver les artefacts de calcul lorsque
l'espace disponible le permet. Les paramètres, mesures, catégories d'échec et
descripteurs nécessaires à l'apprentissage restent consignés.

L'entraînement parcourt les segments et rassemble des mini-lots bornés. Les
points de reprise du modèle permettent de poursuivre un traitement compatible
interrompu. Une reprise ne transforme pas les jeux de validation ou de test en
données de gradient.

Le budget d'entraînement est vérifié entre les mini-lots. Les passes de
préparation, de validation et de contrôle d'intégrité doivent aussi se terminer
avant la publication d'un modèle cohérent : le paramètre `--max-seconds` n'est
pas un chronomètre strict couvrant chacune de ces passes. Le délai global du
processus reste prioritaire. La reprise conserve le curseur des gradients, mais
peut relire des segments pour les vérifier ; elle n'est pas présentée comme
une reprise instantanée au prochain octet du corpus.

La mémoire interactive `experience/`, destinée à retrouver la recette d'une
pièce, reste un mécanisme distinct. Ses limites ne sont pas artificiellement
augmentées à un million pour lui faire jouer le rôle d'une base de corpus.

## Géométries nouvelles et jeux réservés

Les familles géométriques sont affectées à l'entraînement, à la validation ou
au test avant leurs variations de paramètres. Des variantes d'une même
famille ne doivent pas se retrouver de part et d'autre de cette séparation.
Le jeu de test sert au bilan final ; il n'est pas utilisé pour corriger les
poids ni choisir leur activation.

Le contrôle d'un modèle doit distinguer la prédiction du score, le choix
effectif d'une stratégie et l'acceptation du maillage produit. Un faible écart
de prédiction ne prouve pas, à lui seul, une amélioration du maillage. Les
contrôles de qualité natifs restent obligatoires pour chaque résultat.

## Informations locales fournies au réseau

La représentation enrichie contient des descriptions globales et des
empreintes spatiales des entités, des frontières et des zones protégées,
ainsi que des informations topologiques. Le déplacement d'un trou ou d'une
zone de contact peut ainsi modifier la description, même si les dimensions
globales de la pièce restent identiques.

Les empreintes spatiales sont des résumés normalisés. Elles ne sont ni une
occupation volumique exacte ni une reconstruction complète de la CAO, et le
réseau n'est pas présenté comme un réseau de graphes. La recherche continue
repose toujours sur des stratégies réellement maillées et contrôlées ; le
réseau ne place pas directement chaque nœud.

Le petit guide de mémoire utilise 128 entrées, 12 neurones cachés et une sortie,
soit 1 561 paramètres. Le guide de corpus utilise par défaut les mêmes 128
entrées, 32 neurones cachés et une sortie, soit 4 161 paramètres. Ce sont des
modèles distincts ; les dimensions réellement entraînées figurent dans chaque
rapport. Le conseiller historique à 147 paramètres reste séparé.

Un modèle de corpus entraîné est installé comme conseiller de l'atelier. Il
peut ordonner les candidats d'une recherche lorsque son schéma est compatible
et qu'un gain de sélection a été validé. La mémoire locale et les règles de
qualité ne sont pas remplacées. Aucun poids n'est activé simplement parce que
l'entraînement s'est terminé.

## Ce qui reste en Python

| Programme ou rôle | Travail effectué |
| --- | --- |
| `atelier/desktop.py`, `viewport.py`, ressources d'interface | Fenêtre, navigation, formulaires, sélection, projection et dessin de la vue 3D, affichage des diagnostics |
| `atelier/projects.py` | Lecture et sauvegarde JSON ou projet portable, récupération de la source, validation des champs et des chemins |
| `atelier/native.py` | Transport des demandes au processus Rust, copie des fichiers, suivi, annulation et lecture des réponses |
| `atelier/server.py`, `worker.py`, CLI | Transport de l'interface locale et lancement des mêmes opérations natives |
| `atelier/formats.py`, `convert_worker.py` | Adaptation des formats d'export et relecture de leurs données ; ce travail ne remplace pas les contrôles numériques du noyau |
| Lanceurs et installateur | Installation dans l'environnement du projet et démarrage de l'application |

La projection d'un objet à l'écran et la conversion des tableaux nécessaires à
un format d'export font partie de ces rôles autorisés. Aucune triangulation,
génération de corpus, mesure numérique de qualité des éléments, recherche de maillage ou
mise à jour des poids ne doit retomber sur un ancien moteur Python.

Les anciens noms de modules peuvent être conservés comme adaptateurs de
compatibilité ou annoncer une API retirée. Leur présence ne doit pas fournir
une deuxième chaîne numérique Python cachée. Les anciens tests d'algorithmes
du prototype sont à distinguer des tests du produit natif et des tests de
ces adaptateurs.

## Ce qui s'exécute dans le noyau natif

| Composant | Travail effectué |
| --- | --- |
| Rust : `backend.rs`, `quality.rs`, `metrics.rs` | Préparation numérique des demandes, unités et transformations, sélection, qualité, contrôle des zones et conservation du maillage |
| Rust : `corpus.rs`, `corpus_generator.rs` | Planification, génération déterministe, calcul des lots, mesures, segmentation, progression et reprise |
| Rust : `representation.rs` | Construction des descriptions globales, spatiales et topologiques |
| Rust : `experience.rs`, `experience_model.rs`, `dataset_model.rs` | Mémoire des essais, recherche des recettes, entraînement, reprise du modèle, prédiction et bilans réservés |
| Gmsh / OpenCASCADE natifs C/C++, appelés depuis Rust par l'API C | Opérations CAO, réparation prise en charge et algorithmes effectifs de maillage |

Le calcul est donc piloté par Rust, avec des bibliothèques numériques natives
C/C++ ; il ne serait pas exact de présenter Gmsh ou OpenCASCADE comme du code
réécrit en Rust. Leurs bindings Python ne constituent pas le moteur de calcul
du parcours livré.

## Limites qui ne changent pas avec la taille du corpus

Les zones de contact, de raccord et de fortes contraintes sont déclarées ou
doivent provenir d'informations extérieures à la géométrie. Le programme ne
résout pas automatiquement un problème mécanique à partir d'un fichier CAO.
Une géométrie défectueuse peut rester non maillable, et aucune taille de corpus
ne garantit une réparation arbitraire, un maillage tout-hexa ou un optimum
global de discrétisation.

Les mesures réellement exécutées, les tests ignorés, la taille du lot et
l'état d'activation du modèle doivent être lus dans les rapports de livraison.
Un objectif d'un million de configurations affiché dans l'interface ne prouve
ni leur calcul, ni leur validation, ni l'entraînement du modèle sur un million
d'exemples.
