# Correctif de sauvegarde sous Windows — publication rc7

La première publication du 10 novembre 2026 a déclenché les essais GitHub sur
Linux, macOS et Windows, avec Python 3.11 et 3.12. Le
[premier passage](https://github.com/ronanmaudet-sketch/mailleur/actions/runs/42900383130)
a révélé quatre échecs Rust sous Windows : deux lors de l'adoption d'un ancien
corpus, un dans le test de récupération du corpus et un dans la récupération
du journal d'apprentissage. L'erreur signalée était « Access is denied
(os error 5) ».

## Cause et correction

Dans `rust_core/src/corpus.rs`, la synchronisation du registre durable ouvrait
le fichier en lecture seule. Sous Windows, la synchronisation exige un accès
en écriture. L'ouverture corrigée autorise l'écriture sans tronquer le registre.

Dans `rust_core/src/experience.rs`, le journal était ouvert uniquement en ajout.
Sous Windows, ce droit ne permet pas de raccourcir une fin de ligne incomplète
après interruption. Le journal utilise désormais un accès en écriture sans
troncature à l'ouverture, puis un déplacement explicite en fin de fichier avant
l'ajout. Le verrou exclusif de la mémoire protège cette opération.

Ces droits sont documentés par Microsoft pour
[FlushFileBuffers](https://learn.microsoft.com/fr-fr/windows/win32/api/fileapi/nf-fileapi-flushfilebuffers)
et par l'[implémentation Windows de la bibliothèque standard Rust](https://github.com/rust-lang/rust/blob/main/library/std/src/sys/fs/windows.rs)
pour le mode d'ajout qui exclut `FILE_WRITE_DATA`.

La correction conserve les synchronisations sur disque, les empreintes de
contrôle, le refus des données altérées et la publication atomique des index.
Les tests de récupération vérifient la conservation du préfixe durable. Aucun
test n'est désactivé pour faire passer ce correctif.

Les algorithmes de géométrie, de génération des maillages et de contrôle de
leur qualité restent ceux de la rc7. Les archives du 7 novembre et les preuves
Cast3M historiques restent inchangées. Le binaire Mac public est reconstruit
depuis les sources corrigées ; sa nouvelle empreinte figure dans
[BUILD_PUBLIC.json](BUILD_PUBLIC.json).

## Portée des doubles PowerShell

Au [passage suivant](https://github.com/ronanmaudet-sketch/mailleur/actions/runs/42902528550),
les 130 tests Rust passent dans chacun des deux jobs Windows. Les contrats
simulés SOLIDWORKS révèlent ensuite un défaut de capture de variable sous
Windows PowerShell 5.1 : une factory créée dans une portée imbriquée peut
renvoyer une valeur nulle au lieu du double d'application attendu.

Le script `scripts/converters/test_solidworks_bridge.ps1` capture désormais
explicitement l'objet reçu en paramètre dans une variable locale dédiée.
Une assertion vérifie son identité malgré des variables homonymes nulles
dans la portée appelante. Les neuf scénarios et leurs critères sont conservés ;
le convertisseur COM de production reste inchangé. Le workflow exécute les tests
Python avant ces contrats et conserve le contrôle PowerShell après un échec
indépendant, afin de recueillir les deux résultats sans masquer un échec.

## Validation

La régression locale sur Mac ARM64 du binaire corrigé a réussi : **132 tests
Rust en 7,12 s**, **200 tests Python en 31,270 s** et **43 comparaisons Cast3M**
sur l’éprouvette quadratique (0,4357 s). Le binaire, les résultats et les
validations précédentes sont identifiés dans le
[rapport de publication](../../verification/publication_20261110/README.md).
Cette régression Mac reste attribuée à son système d’exécution.

Les résultats de chaque révision de la matrice sont consultables dans
[GitHub Actions](https://github.com/ronanmaudet-sketch/mailleur/actions).
Le rapport conserve aussi le snapshot des échecs Windows du premier passage.
Les essais Windows n’installent pas SOLIDWORKS, Abaqus, ANSYS ou d’autres
solveurs commerciaux : les contrats SOLIDWORKS de cette suite utilisent des
doubles de test. La liste des formats et les limites des essais réels restent
décrites dans la documentation du projet.
