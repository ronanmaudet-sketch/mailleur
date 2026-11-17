# Vérifications rc8 du 17 novembre 2026

Sur le Mac ARM64 de préparation, la révision corrigée passe les 132 tests Rust et les 200 tests Python. La fumée native génère une plaque de 25 nœuds et 32 éléments. Le parcours réel Cast3M 2026.0 passe ses 43 contrôles avec le noyau rc8.

Les temps, l’empreinte du noyau et les empreintes des journaux locaux figurent dans [summary.json](summary.json). Les tests locaux utilisent les dépendances de l’environnement de développement existant. Le temps Cast3M couvre le lancement, l’import, les contrôles et la résolution de cette éprouvette.

Les [résultats de chaque révision dans GitHub Actions](https://github.com/ronanmaudet-sketch/mailleur/actions/workflows/tests.yml?query=branch%3Amain) complètent ces contrôles avec les environnements Windows, Ubuntu et macOS préparés par les runners. Le [détail de la correction](../../docs/CORRECTIONS_RC8.md) explique le défaut observé et les modifications.
