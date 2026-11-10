# Optimisations de la rc5

**Portée de cette copie publique.** Les mesures et commandes ci-dessous sont
historiques. Les données brutes, les anciennes sources rc4 et certains fichiers
de comparaison ne sont pas inclus dans ce dépôt léger ; consulter la
[notice des preuves externes](provenance/PREUVES_EXTERNES.md) avant de tenter
leur reproduction. Aucun nouveau résultat n’est déduit de cette publication.

La rc5 travaille sur les coûts d'exécution du projet rc4 : traitement des
campagnes, entraînement, contrôles de qualité et affichage. Une réduction de
temps ou de mémoire dans une mesure ne constitue pas une amélioration de la
précision mécanique ni une preuve de généralisation du réseau.

Les comparaisons doivent employer la même géométrie, les mêmes paramètres,
la même graine et les mêmes données. Les sources rc4 de référence, compilées
avec le même environnement que la rc5, et les mesures brutes permettent de
distinguer un changement d'algorithme d'un changement de machine, de
compilation ou de données.

## Entraînement par mini-lots

Les buffers de calcul et de lecture sont réutilisés pour limiter les
allocations temporaires par exemple. Les évaluations compatibles sont
regroupées afin d'éviter de parcourir plusieurs fois les mêmes données pour
le même réseau. Le jeu de test reste exclu des gradients et de la sélection
des poids.

L'entraînement continue de lire les segments progressivement. Il ne charge
pas un million de cas dans une collection Python ou dans un tableau Rust
unique. Les limites des mini-lots et des lignes JSONL restent contrôlées.

### Mesure du parcours JSONL complet

La [comparaison de l'entraînement de corpus (archive complète)](provenance/PREUVES_EXTERNES.md)
parcourt 33 010 cas synthétiques, soit 66 020 exemples de stratégies et
187,14 Mo de JSONL. Elle utilise 128 entrées, 32 neurones cachés, trois
passages et des mini-lots de 128 exemples. Le code de référence et le code
optimisé sont compilés avec le même compilateur, les mêmes dépendances et le
même profil release.

Sur trois répétitions par variante, la médiane du parcours complet passe de
17,428 à 8,942 s, soit environ 1,95 fois plus rapide pour ce jeu. Les poids,
l'empreinte du jeu, les métriques d'entraînement, de validation et de test,
la stratégie de référence et la décision d'activation sont identiques.

Ce scénario inclut la préparation et les évaluations du corpus JSONL. Les
enregistrements restent des fixtures synthétiques : cette mesure ne produit
pas 33 010 maillages et ne démontre pas un gain de qualité du réseau.

### Mesure de l'apprentissage interactif

La [comparaison des évaluations du guide (archive complète)](provenance/PREUVES_EXTERNES.md)
utilise 8 192 exemples synthétiques de 128 entrées, 12 neurones cachés et
16 passages d'entraînement. Sur cinq répétitions, la médiane passe de
0,782 s à 0,519 s, soit environ 1,51 fois plus rapide pour cette fixture.
Les modèles JSON obtenus par les deux implémentations sont identiques.

Une [mesure avec reprise de poids existants (archive complète)](provenance/PREUVES_EXTERNES.md)
utilise la même taille de fixture, 16 passages et cinq répétitions. La médiane
passe de 1,007 à 0,511 s, soit environ 1,97 fois plus rapide. L'indicateur
`warm_started` est vrai et les modèles JSON finaux sont identiques.

Cette mesure porte sur l'actualisation du petit guide interactif. Elle
n'inclut pas la génération d'une nouvelle géométrie ou de son maillage.

La [mesure de stockage (archive complète)](provenance/PREUVES_EXTERNES.md)
utilise un index de 5 000 observations. Le JSON compact conserve les mêmes
données après décodage et réduit le fichier de 13,35 à 4,38 Mo. La médiane
de cinq écritures avec synchronisation passe de 50,1 à 33,7 ms. Le pic
d'allocations supplémentaires du sérialiseur passe de 16,78 Mo à 65,6 ko ;
il ne s'agit pas de la mémoire totale du logiciel ou de Gmsh.

La construction des métadonnées d'un événement évite aussi de copier puis
supprimer l'historique complet. Cette micro-mesure est distincte d'un temps
d'apprentissage et ne doit pas être ajoutée à son accélération.

La [vérification de compatibilité (archive complète)](provenance/PREUVES_EXTERNES.md)
enchaîne une recherche rc4, un réemploi rc5 et une relecture de la mémoire
compacte par rc4. Le contexte est retrouvé dans les deux sens ; chaque
réemploi recalcule le maillage. Cet essai conserve cinq événements et produit
le même résultat de 302 éléments, avec une qualité minimale de 0,827682.
La version d'identité numérique des contextes reste celle de la rc4 pour
cette livraison consacrée aux performances. Une future modification des
critères ou des algorithmes numériques devra traiter cette compatibilité
explicitement ; changer seulement le numéro de l'application ne suffit pas.

## Reprise du corpus

Le point de reprise peut mémoriser une position durable dans le segment et
l'empreinte de son préfixe. Une reprise vérifie ce préfixe, puis décode les
lignes effectivement ajoutées après le point enregistré. Un ancien point
de reprise rc4 peut demander une relecture initiale de migration.

Cette méthode réduit le travail de désérialisation ; elle ne supprime pas la
lecture nécessaire au contrôle d'intégrité. Une fin de ligne partielle reste
un cas de récupération distinct d'une altération de données déjà validées.
Une altération du préfixe doit être signalée sans supprimer les fichiers.

La [mesure de reprise (archive complète)](provenance/PREUVES_EXTERNES.md)
utilise un segment synthétique déjà validé de 1 000 enregistrements,
soit 3,36 Mo, et cinq répétitions en release. La médiane passe de
15,524 à 2,753 ms, soit environ 5,64 fois plus rapide. Les compteurs sont
identiques et l'empreinte SHA-256 du préfixe reste vérifiée. Dans ce scénario
sans nouvelle ligne, le curseur évite de désérialiser les 1 000 anciens cas.
Cette mesure ne comprend aucun calcul de maillage.

## Qualité des éléments

Les contrôles peuvent réutiliser la connectivité déjà extraite du moteur
natif, au lieu de la demander une seconde fois. Les zones sans marge peuvent
utiliser un index d'incidence entre nœuds et éléments. Les zones avec marge
conservent leur sélection conservatrice par boîtes englobantes.

Les comptes portent toujours sur tous les éléments du domaine. Les limites
des listes affichées et des échantillons destinés au raffinement ne limitent
pas ces comptes. Les éléments appartenant à plusieurs zones ne doivent pas
être comptés plusieurs fois dans le total des violations protégées.

La [comparaison native de qualité (archive complète)](provenance/PREUVES_EXTERNES.md)
contrôle six cas, avec égalité exacte des rapports de qualité, y compris
l'ordre et les nombres flottants. Le cas répété trois fois est une boîte
de 45 183 tétraèdres, avec douze zones de contact, dont des zones recouvrantes.

| Mesure sur cette boîte, médiane | rc4 | rc5 |
| --- | ---: | ---: |
| Phase de validation | 282,35 ms | 115,73 ms |
| Temps global mesuré | 1,452 s | 1,348 s |
| Pic de mémoire RSS du processus | 114 476 Kio | 116 400 Kio |

La phase de validation est environ 2,44 fois plus rapide sur ce cas, mais le
gain global reste plus faible : la génération du maillage et les écritures
font également partie du traitement. Le pic RSS mesuré augmente légèrement ;
aucune réduction de mémoire globale du processus n'est revendiquée.
Les cinq autres cas n'ont qu'une mesure par variante et ne permettent pas
d'annoncer un gain de temps robuste.

Un contrôle séparé sur 120 000 éléments synthétiques et seize zones compare
le balayage et la construction/utilisation de l'index. Il vérifie la même
sélection, mais sa durée provient d'une seule exécution : ce n'est pas un
benchmark global du mailleur.

## Affichage

La vue conserve les données d'affichage préparées et réutilise les
projections lorsqu'une modification ne les affecte pas. Une modification de
la caméra, du zoom, du déplacement ou de la taille de la fenêtre invalide
les caches concernés. La sélection réutilise la projection disponible et
doit garder le choix de la face visible au premier plan.

Ces calculs Python concernent la caméra, le rendu et les interactions de
l'interface. La géométrie de calcul, le maillage, la qualité, le corpus et
l'apprentissage restent traités par Rust, qui pilote Gmsh/OpenCASCADE natifs.

### Mesure de la vue

La [mesure Qt offscreen (archive complète)](provenance/PREUVES_EXTERNES.md) utilise
une fixture d'affichage synthétique de 10 201 points et 20 000 triangles,
dans une vue de 1 000 × 760 pixels. Elle porte uniquement sur l'interface.

| Opération | rc4 | rc5 |
| --- | ---: | ---: |
| Premier affichage de la fixture | 244,3 ms | 269,6 ms |
| Rafraîchissement inchangé, médiane | 273,5 ms | 0,350 ms |
| Rafraîchissement après rotation, médiane | 228,5 ms | 202,3 ms |
| Rafraîchissement après déplacement, médiane | 236,3 ms | 154,3 ms |
| Ensemble de 270 requêtes de sélection | 13,584 s | 0,100 s |

La préparation initiale est ici plus lente, tandis que les réutilisations du
cache réduisent les opérations suivantes. Le rafraîchissement inchangé
réutilise une image déjà préparée ; son temps ne décrit pas le coût d'une
rotation de la pièce. Les 270 sélections donnent les mêmes résultats.

Le rendu n'est pas identique pixel par pixel : la différence moyenne mesurée
vaut 0,092 niveau sur 255 et le maximum quatre niveaux. Ce contrôle ne mesure
ni la précision géométrique du maillage ni la vitesse sur un autre ordinateur.

## Échanges de fichiers et campagnes sans conservation des maillages

Le pont Python demande une réponse native compacte, puis lit le rapport
enregistré. La [mesure de transport (archive complète)](provenance/PREUVES_EXTERNES.md)
compare les deux clients sur le même exécutable Rust, avec trois séries de
vingt courtes commandes de métriques. La médiane par appel passe de 82,78 à
3,03 ms et les rapports natifs sont égaux. Le volume de journal mesuré pour
vingt commandes passe de 51 940 à 240 octets. Ce scénario mesure le transport
et l'attente du processus, pas une accélération du calcul de maillage.

Le hachage d'un fichier source se fait par blocs au lieu de charger le
fichier complet. Sur le fichier de 16 Mio de cette mesure, l'empreinte reste
identique et le pic d'allocations Python supplémentaires passe de 16,78 à
2,10 Mo. Cette valeur ne représente pas la mémoire totale de l'application.

Quand les fichiers de maillage ne sont pas conservés pour un corpus, le
moteur peut éviter de sérialiser les artefacts d'affichage et les fichiers
temporaires lourds. La [mesure du mode rapport seul (archive complète)](provenance/PREUVES_EXTERNES.md)
compare ce mode et la production complète sur la même boîte de 45 183
tétraèdres et douze zones, avec trois répétitions. La médiane passe de
1,123 à 0,816 s ; les artefacts lourds passent de 10,40 Mo par évaluation à
zéro. Les champs de qualité, les comptes, groupes et sélections comparés
sont égaux. Les rapports et les données nécessaires au corpus restent
enregistrés ; il ne s'agit pas d'une suppression de toute écriture disque.

Le profil de cette mesure utilise des rapports diagnostiques (`strict=false`)
pour comparer les mesures. Une qualité rejetée reste rejetée et ne devient
pas exportable. L'affichage interactif et la génération publique conservent
leurs fichiers lorsqu'ils sont nécessaires.

La conversion d'unités des exports utilise une transformation native globale
après vérification de la finitude des coordonnées, puis remet à jour les
transformations périodiques. Aucun gain de temps spécifique à cette dernière
modification n'est annoncé sans mesure correspondante.

## Reproduire les mesures

Exécuter les commandes suivantes depuis la racine du projet, avec son
environnement Python et Cargo disponibles. La référence Rust rc4 est fournie
en sources dans `verification/optimization/reproduction/rust_rc4` : elle
contient `src`, `Cargo.toml` et `Cargo.lock`, sans binaire ni cache de
compilation préconstruit. Le script d'entraînement compile les variantes
comparées dans le même environnement.

```sh
python scripts/benchmark_training.py \
  --baseline-root verification/optimization/reproduction/rust_rc4 \
  --cases 33010 --epochs 3 --batch-size 128 --repetitions 3 \
  --output verification/optimization/training_reproduced.json

python scripts/benchmark_viewport.py \
  --output verification/optimization/viewport_reproduced.json

python scripts/benchmark_transport.py \
  --output verification/optimization/transport_reproduced.json

python scripts/benchmark_bulk.py verification/optimization/bulk.json \
  --repetitions 3 --output verification/optimization/bulk_reproduced.json
```

Le script d'entraînement crée sa fixture pendant la mesure ; le fichier
JSONL de 187 Mo n'est pas inclus dans l'archive. Il utilise un dossier
temporaire, supprimé à la fin par défaut. La compilation et cette préparation
nécessitent de l'espace temporaire et peuvent demander le téléchargement des
dépendances Cargo si elles ne sont pas déjà présentes. L'option `--offline`
est disponible lorsque les dépendances sont déjà en cache.

La mesure de vue utilise Qt offscreen par défaut. Les scripts de vue et de
transport disposent de leurs anciennes sources Python de référence dans le
dossier `reproduction`. Le script bulk prend le projet consigné dans son
rapport d'origine pour comparer les deux modes sur la même géométrie.
Les sorties `*_reproduced.json` laissent les rapports de livraison intacts.

## Contenu de l'archive

Par défaut, l'empaquetage conserve les sources, tests, ressources nécessaires
au fonctionnement et preuves courantes. Il exclut les anciennes campagnes
de validation qui ne sont plus référencées. Sur la mesure d'empaquetage,
cela ramène le ZIP d'environ 42 à 20 Mo ; ce gain concerne les historiques
de calcul, pas une suppression du code ou des fonctions du logiciel.
La taille finale peut varier légèrement avec les documents et rapports
ajoutés à la livraison.

```sh
python scripts/package_native_release.py --output output/Atelier_Mailleur_rc5.zip

python scripts/package_native_release.py --include-history \
  --output output/Atelier_Mailleur_rc5_avec_historiques.zip
```

`--include-history` ajoute les anciennes campagnes encore présentes dans
le dossier de travail ; il ne récupère pas des fichiers absents. Les options
d'empaquetage changent le contenu du ZIP et ne suppriment pas les fichiers
du dossier de travail.

## Portée des mesures

L'environnement relevé pendant cette livraison est Linux x86_64
(noyau 6.18.57, glibc 2.39), sur processeur AMD EPYC 9V74. Il utilise
Rust 1.99.1, Python 3.12.14, PySide6 6.12.0, Gmsh 4.15.2 et meshio 5.3.5.
Les mesures de l'interface emploient Qt offscreen. Ces durées ne constituent
pas une mesure sur le Mac de l'utilisateur.

Les résultats de validation et les mesures de performance sont consignés
séparément. Le rapport doit préciser le processeur ou l'environnement,
le compilateur, la taille des fixtures, le nombre de répétitions et la
statistique retenue. Les temps de compilation et de préparation ne doivent
pas être présentés comme des temps de calcul du maillage.

Le plan de 250 000 configurations structurées et 750 000 aléatoires reste
un objectif configurable. Une mesure sur des journaux ou des enregistrements
synthétiques n'est pas un calcul de ce million de maillages. Les validations
macOS, Windows et dans les solveurs externes doivent être distinguées des
tests Linux exécutés pendant la livraison.

## Validation de la livraison

Le [rapport natif de la rc5](../verification/native_release/summary.json),
daté du **4 novembre 2026**, consigne 150 tests Python et 110 tests Rust
réussis, sans test ignoré. Le [pilote réel du corpus (archive complète)](provenance/PREUVES_EXTERNES.md)
traite 48 configurations, dont 24 structurées et 24 aléatoires ; ses
96 résultats de stratégies ne sont pas un calcul du million planifié.

Les six géométries de l'apprentissage continu ont été réévaluées, ainsi que
le réemploi d'une recette et le refus de critères impossibles. Les tests
des optimisations ajoutent notamment le refus d'un préfixe durable altéré,
la cohérence du mode rapport seul et les invalidations de la vue.
Les 57 tests du prototype Python retirés dans la rc4 restent des références
textuelles et ne sont pas comptés comme de nouvelles réussites de la rc5.

Le [contrôle après extraction neuve (archive complète)](provenance/PREUVES_EXTERNES.md)
vérifie l'intégrité de l'archive et utilise le noyau rc5 embarqué. Il couvre
le plan d'un million sans calcul, l'aperçu aléatoire au bon indice, deux cas
réels, l'entraînement par mini-lots et l'apprentissage avec export MSH.
Ce dernier parcours produit un résultat accepté de 1 847 éléments.
