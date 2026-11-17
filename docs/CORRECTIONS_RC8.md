# Corrections rc8 — 17 novembre 2026

Cette préversion corrige les échecs Windows observés dans la suite
[GitHub Actions de la rc7](https://github.com/ronanmaudet-sketch/mailleur/actions/runs/43708610956).
Les contrôles de chaque révision sont disponibles dans
[le workflow multiplateforme](https://github.com/ronanmaudet-sketch/mailleur/actions/workflows/tests.yml?query=branch%3Amain).

## Fermeture du moteur natif sous Windows

Une exécution instrumentée sur Windows a achevé les appels de génération,
de contrôle du maillage et `gmshFinalize`, puis s'est bloquée lors du
déchargement de la bibliothèque Gmsh. Le processus atteignait alors le délai
maximal de l'appel. Le
[diagnostic Rust](https://github.com/ronanmaudet-sketch/mailleur/actions/runs/44169187973)
conserve les traces de ces étapes.

Le correctif conserve le module Gmsh chargé jusqu'à la fin du processus
Windows. Les contextes continuent d'appeler `gmshFinalize` pour libérer les
données de calcul entre deux opérations. La durée de chargement du module est
gérée par le mécanisme Windows de
[module résident](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-getmodulehandleexw).

## Lecture des fichiers de test en UTF-8

Les fichiers JSON produits par le noyau et les sources Python sont lus avec
un encodage UTF-8 explicite dans les tests. Cela corrige notamment la comparaison
du modèle partagé, dont les textes accentués étaient décodés avec l'encodage
système Windows. La mutation du corpus de test écrit également en UTF-8.

## Test d'arrêt du worker HTTP

Le double de processus du test remplace uniquement le worker Mailleur.
Il laisse la commande Windows `taskkill` arrêter le véritable processus lent.
Le délai et les assertions du test vérifient toujours l'arrêt effectif du worker.

## Diagnostic de la CI

Avant la suite Python, `scripts/check_native_smoke.py` génère une petite plaque
par `NativeSession.generate`. Il contrôle les nœuds, les éléments, la qualité
et le fichier de maillage, avec un budget de trente secondes. Il conserve le
journal natif et un diagnostic JSON. GitHub joint ce dossier aux artefacts du
job après un échec. Les six combinaisons macOS, Ubuntu et Windows avec
Python 3.11 et 3.12 et les neuf contrats PowerShell restent dans la suite.

## Versions et provenance

Le paquet Python porte la version `1.0.0rc8` et le noyau Rust `1.0.0-rc.8`.
Les identifiants de provenance des algorithmes numériques restent ceux de la
rc7, puisque les changements portent sur le cycle de vie de la bibliothèque
et les outils de vérification. Les données historiques et les poids conservés
gardent ainsi leur provenance. La construction du binaire Mac est décrite dans
[BUILD_PUBLIC.json](provenance/BUILD_PUBLIC.json).
