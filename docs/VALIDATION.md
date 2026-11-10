# Protocole de validation

La révision locale du 4 novembre 2026 dispose aussi de
[mesures et résultats Apple M3](MESURES_M3.md) : 163 tests Python et 111 tests
Rust réussis, sans test ignoré. Les rapports initiaux Linux restent conservés
dans leurs dossiers d'origine ; les preuves M3 sont dans `verification/local_m3/`.

Cette page définit les contrôles à exécuter pour une livraison. Elle ne remplace
pas les sorties de test : le nombre de tests, leurs durées, versions et résultats
doivent provenir du rapport réellement produit dans `verification/`.

## Contrôles de non-régression des optimisations rc5

Les comparaisons rc4/rc5 doivent séparer la compilation, la préparation et le
traitement mesuré. Utiliser les mêmes données, paramètres, graines, versions
de dépendances et conditions d'exécution. Consigner les répétitions et la
statistique retenue, puis vérifier les sorties avant de publier une durée.

Pour l'entraînement, contrôler les poids, les métriques, l'activation et
l'indépendance du jeu de test. Pour le corpus, vérifier les anciennes reprises,
les fins partielles et l'altération d'un préfixe durable sans suppression de
données. Pour la qualité, vérifier les comptes complets, les zones
recouvrantes et l'ordre déterministe des échantillons. Pour la vue, contrôler
les invalidations de cache et le choix de la face visible lors de la sélection.

Le [guide des optimisations](OPTIMISATIONS_RC5.md) décrit la portée de ces
mesures. Les résultats de livraison ci-dessous concernent la rc5 ; les
comparaisons avec la rc4 sont conservées dans les rapports de performance.

## Contrôles du corpus

Le plan par défaut doit indiquer 1 000 000 de cas, dont 250 000 structurés et
750 000 aléatoires, tout en conservant zéro calcul exécuté au moment de sa
création. L'espace et le temps de cette planification doivent être indépendants
du nombre de géométries qui seraient effectivement calculées.

Les tests de génération vérifient la reproductibilité par graine et indice,
les modes plan/surface/volume et la séparation des familles entre entraînement,
validation et test. Les calculs de livraison restent de petits lots mesurés.
Un cas géométriquement invalide doit produire une catégorie d'échec, sans
maillage présenté comme accepté.

Une reprise doit être testée dans une nouvelle session et après une fin de
segment partiellement écrite. Les cas déjà terminés ne doivent pas devenir des
doublons. L'annulation doit libérer le processus et le verrou pour permettre
un nouveau lot.

L'entraînement doit parcourir les segments en mini-lots bornés, conserver un
point de reprise et garder le jeu de test hors des gradients et du choix des
poids. Le bilan du choix des stratégies doit être séparé de la seule erreur
quadratique de prédiction.

Les tests de frontière Python vérifient que les anciens noms de modules ne
contournent pas le processus Rust. Les tests numériques du prototype Python
retiré ne doivent ni échouer silencieusement ni être ignorés puis comptés comme
preuves du nouveau produit : leur remplacement par des tests de compatibilité
ou des opérations natives est un changement de contrat explicite.

Les résultats rc5 ci-dessous proviennent de leur exécution réelle. Voir aussi
[le guide du corpus et des langages](CORPUS_ET_LANGAGES.md).

## Résultats exécutés pour la rc5

La livraison **1.0.0rc5 du 4 novembre 2026** a été vérifiée sous Linux x86_64,
avec Python 3.12.14. Les données ci-dessous proviennent du
[rapport natif](../verification/native_release/summary.json), du
[rapport d'apprentissage continu (archive complète)](provenance/PREUVES_EXTERNES.md)
et du [rapport du corpus (archive complète)](provenance/PREUVES_EXTERNES.md).

| Vérification | Résultat observé |
| --- | --- |
| Suite Python | 150 tests, code de retour 0, aucun test ignoré |
| Suite Rust | 110 tests réussis, aucun échec, aucun test ignoré |
| Retrait de l'ancien moteur Python | Statut hérité de la rc4 : 57 anciens tests restent des références textuelles ; ils ne sont pas comptés comme de nouvelles réussites ; les 16 tests de remplacement restent exécutables |
| Plan d'un million | 250 000 cas structurés + 750 000 aléatoires ; zéro calcul exécuté, trois fichiers et 5 058 octets |
| Pilote réellement calculé | 48 cas : 24 structurés et 24 aléatoires, 24 familles distinctes |
| Production de maillages | 46 cas ont produit un maillage ; 45 ont au moins une stratégie admissible |
| Stratégies | 96 résultats : 88 acceptations, 8 refus |
| Appels natifs | 140, dont 48 inspections de référence ; ce ne sont pas 140 maillages acceptés |
| Reprise du pilote | Deux lots de 24 cas ; le second dans une nouvelle session Python, sans trou ni doublon |
| Description du corpus | 128 entrées, schéma `atelier.geometry_action_features.v2` |
| Apprentissage de corpus | Deux passages, mini-lots de 16 ; 32 cas d'entraînement, 8 de validation, 8 de test |
| Exemples par partition | 64 exemples de stratégies d'entraînement, 16 de validation, 16 de test |
| Réseau de corpus | 128→32→1, 4 161 paramètres ; jeu de test hors des gradients et de la sélection des poids |
| Recherche sur la pièce courante | 6 géométries distinctes : plaque percée, plaque chanfreinée, équerre, sphère surfacique, polygone concave et STEP à deux volumes |
| Journal d'apprentissage | 29 essais, dont le réemploi et les refus attendus |
| Mémoire finale | 9 contextes ; 27 observations acceptées et 2 refusées |
| Modèle cumulatif | 128→12→1, 1 561 paramètres ; 9 versions, reprise des poids confirmée ; 29 exemples, dont 25 d'entraînement et 4 de validation |
| Réouverture de la recette gagnante | Nouvelle session Python, mémoire retrouvée, maillage recalculé et accepté en 1 essai |
| Modification de zone protégée | Contexte différent, identité géométrique conservée et ancienne acceptation non réutilisée |
| Seuil volontairement impossible | Refus confirmé après 2 essais ; aucun `mesh.msh` public |

Le réseau de corpus est **entraîné mais désactivé pour le classement**. Le
score moyen de la stratégie sélectionnée en validation vaut environ 0,752145,
identique à la référence fixe `automatic_base` ; le gain est donc 0,0. Les
poids ont été sauvegardés et installés dans l'atelier, mais leur présence ne
suffit pas à les activer.

Le réseau de mémoire est également **désactivé pour le classement** : son
erreur quadratique de validation vaut environ **0,032858**, contre **0,006249**
pour la référence constante. Une erreur plus faible étant préférable, aucun
gain n'est établi. La recherche des recettes et le réemploi exact revalidé
restent actifs. Ces petits pilotes ne sont pas des essais industriels
indépendants et ne démontrent pas la couverture d'un million de configurations.

La suite contient un test d'entraînement sur **33 010 enregistrements JSONL
synthétiques**, soit 66 020 exemples de stratégies, avec des mini-lots bornés.
Ces enregistrements ne sont pas des maillages calculés et ne sont pas ajoutés
aux 48 cas du pilote natif.

Les 57 tests retirés du prototype Python ne sont comptés ni comme réussis ni
comme ignorés. Leur remplacement dans la rc4 change le périmètre : les totaux
actuels ne se comparent pas directement aux 160 tests de la rc3. Le détail est
conservé dans [la référence des tests retirés](../tests/retired_reference/README.txt)
et dans `test_scope` du rapport natif.

Les commandes de la CLI release avaient également été vérifiées dans une
campagne distincte de la rc4, conservée à titre historique :
[plan (archive complète)](provenance/PREUVES_EXTERNES.md),
[lot (archive complète)](provenance/PREUVES_EXTERNES.md),
[état (archive complète)](provenance/PREUVES_EXTERNES.md) et
[entraînement (archive complète)](provenance/PREUVES_EXTERNES.md). Ces sorties ne sont pas
additionnées aux 48 cas du pilote rc5 ci-dessus et ne sont pas présentées comme
de nouvelles exécutions de la CLI rc5.

Le conseiller historique sur corpus synthétique reste également désactivé,
son gain de validation n'étant pas positif. Ses résultats figurent séparément
dans le rapport natif et ne doivent pas être confondus avec la mémoire continue.

Les essais macOS, Windows et dans Code_Aster, Salome-Meca, CalculiX, Abaqus,
ANSYS ou Cast3M **n'ont pas été exécutés** dans cette livraison. Les sources et
la matrice CI préparée permettent de poursuivre ces validations ; ils ne
remplacent pas leurs résultats.

## Environnement à relever

```sh
python --version
python -m atelier doctor
python -m pip freeze
```

Consigner le système, l'architecture, la version de Python, Gmsh, OpenCASCADE
si exposée, meshio, h5py et NumPy. Relever les bibliothèques système nécessaires
et la commande exacte de lancement.

## Contrôles de la livraison

| Domaine | Contrôle attendu | Preuve à conserver |
| --- | --- | --- |
| Sources | Compilation des modules et présence des ressources web | Sortie de compilation et inventaire de l'archive |
| Régressions | Suite `unittest` historique et nouveaux tests | Sortie complète et code de retour |
| Pièces 2D | Trous préservés, congés/chanfreins, contours valides, groupes | Paramètres, rapport, maillage relu |
| Surfaces | CAO surfacique et STL ouvert, diagnostics d'ouverture | Source d'essai, rapport et aperçu |
| Volumes | Solides fermés, tétraèdres valides, refus d'une surface ouverte | Source, rapport de qualité ou échec attendu |
| Réparation | Options désactivées/activées, tolérance, compte rendu avant/après | Projet et rapport |
| Ordre 2 | Connectivité d'ordre supérieur conservée ou format explicitement refusé | Relecture indépendante du fichier |
| Projets | Sauvegarde et réouverture avec la source incluse | Projet portable et empreintes de sa source |
| Application | Import, génération, progression, annulation, exports | Test API et contrôle des messages de l'interface |
| Exports | Comptage, coordonnées, types et groupes après relecture | Manifeste par format et résultats des tests |
| Guide historique | Familles séparées, comparaison avec référence fixe, résultat négatif conservé | Corpus, paramètres, rapport et modèle |
| Nouvelle géométrie | Recherche bornée sur la pièce ouverte et une CAO importée | Source, essais complets, recette retenue ou refus motivé |
| Mémoire | Réouverture dans une nouvelle session ; réemploi remaillé et revalidé | Deux rapports, identités de contexte et sources |
| Changement de contexte | Source, unités, zones et paramètres modifiés sans collision de mémoire | Empreintes et rapports comparés |
| Apprentissage cumulatif | Reprise de version compatible et replay des observations | Versions modèle, nombre d'exemples et rapport de validation |
| Qualité spatiale | Zone protégée active, seuil violé, tag absent, marge et contrôles complets | Rapports par zone et refus attendus |
| Aucun candidat acceptable | Aucune exportation présentée comme maillage accepté | Rapport d'échec et contenu du dossier résultat |
| Archive | Extraction propre puis calcul depuis le dossier extrait | Empreintes et sortie de la commande de vérification |

Exécution de la suite :

```sh
python -m unittest discover -s tests -v
```

Un test d'intégration dépendant de Gmsh ignoré pour dépendance absente ne prouve
pas que cette intégration fonctionne. Le rapport doit indiquer séparément les
tests réussis, ignorés et échoués.

## Essais de recherche et de mémoire continue

Le test d'une première pièce, puis de la même pièce dans une nouvelle session,
doit distinguer le chargement de mémoire, le recalcul de la recette et son
contrôle de qualité. Le nombre d'essais effectivement exécutés doit rester
compatible avec le budget annoncé.

Une source importée doit être essayée avec le même nom mais un contenu changé,
et avec le même contenu sous un autre nom. Le contexte doit également être
modifié par une zone ou un seuil plus strict pour vérifier que l'ancienne
acceptation n'est pas réutilisée à tort.

La qualité des zones doit être testée sur un cas acceptable et un cas refusé.
Un seuil volontairement impossible ne doit pas être masqué par une bonne
moyenne globale. Une entité inexistante ou une zone sans éléments concernés
ne constitue pas une zone protégée réussie. Le rapport doit permettre de voir
quels éléments ont effectivement été évalués.

Le replay et les versions du modèle doivent être vérifiés séparément de la
recherche. La présence d'un fichier modèle ne prouve pas son activation, ni
un gain sur des géométries nouvelles. Les jeux réservés à la validation doivent
rester distincts par géométrie et le rapport doit indiquer l'insuffisance de
données lorsque celle-ci empêche une conclusion.

Ces contrôles définissent les invariants du parcours continu. Leur exécution
rc4 du 3 novembre 2026 est consignée dans le
[rapport d'apprentissage continu (archive complète)](provenance/PREUVES_EXTERNES.md)
et le [rapport natif](../verification/native_release/summary.json). Les sorties
conservées d'une version antérieure ne prouvent pas les fonctions ajoutées à
la version courante.

## Essais dans les solveurs

Les relectures de formats doivent être distinguées des essais effectués dans
Code_Aster, Salome-Meca, CalculiX, Abaqus, ANSYS ou Cast3M. Pour chaque essai
solveur, relever sa version, les éléments importés, les groupes reconnus et
l'absence de modification involontaire de formulation.

Une petite analyse de référence et une étude de convergence sont plus
informatives qu'une ouverture visuelle seule. Les essais dans un solveur qui
n'est pas installé dans l'environnement de livraison restent à réaliser et
ne doivent pas être annoncés comme réussis.

## Matrice multiplateforme préparée

Le workflow `.github/workflows/tests.yml` prévoit Ubuntu, macOS et Windows,
avec Python 3.11 et 3.12. Il doit être exécuté dans un dépôt GitHub pour produire
ses résultats. Sa présence dans l'archive ne constitue pas une exécution.

La distribution native, la signature macOS, l'installeur Windows et les tests
sur des matériels non représentés par cette matrice constituent des validations
distinctes.
