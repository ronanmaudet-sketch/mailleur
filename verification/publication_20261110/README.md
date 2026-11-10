# Vérification de la copie de publication

## Correctif de sauvegarde Windows — régression locale Mac

Le binaire Mac public corrigé porte le SHA256
`85e3952da15bb445ebe00c003f48644444c430f5ff68779778e7cae186319aa6`
(2 309 376 octets). Il remplace la première construction publique
`598732bd9fa802acc22b424fde5072a0a0d56fe57851b557a0a88968ee3e61d8`.
Les modifications concernent les droits d’ouverture des fichiers de reprise ;
les algorithmes de géométrie, maillage et qualité restent ceux de la rc7.

Le 10 novembre 2026, sur Mac ARM64 :

| Contrôle exécuté avec les sources/binaire corrigés | Résultat |
| --- | --- |
| Suite Rust release | **132 réussis**, 0 échec, 0 ignoré ; **7,12 s** |
| Suite Python | **200 réussis**, 0 échec/erreur, 0 ignoré ; **31,270 s** |
| Éprouvette réelle Cast3M 2026.0 | **43/43 comparaisons**, 497 nœuds et 221 TE10 ; **0,4357 s** |

Les dépendances proviennent du venv de développement existant ; ce n’est pas
une installation vierge supplémentaire. Le temps Cast3M comprend lancement,
lecture, contrôles et résolution d’un seul cas affine. Il ne mesure aucune
accélération du solveur. Le retour et le niveau d’erreur valent zéro ; le
drapeau Fortran `IEEE_INVALID_FLAG` reste enregistré. Les fichiers d’entrée
n’ont pas changé pendant le calcul.

Une première tentative Python dans un contexte d’exécution restreint s’est
arrêtée après **188 tests avec 7 erreurs de permission** (sockets locales et
commande `ps`). La même commande, relancée avec les autorisations système
nécessaires, a ensuite exécuté les 200 tests ci-dessus. Les deux journaux sont
conservés dans la préparation locale ; leurs empreintes figurent dans le
[résumé](summary.json).

## GitHub Actions et portée Windows

Le [premier état de la CI](github_actions_initial.json), daté dans le fichier,
conserve les quatre échecs Rust observés dans chacun des deux jobs Windows.
Les étapes Python de ces jobs n’avaient pas été exécutées. Les jobs encore
en file d’attente dans ce snapshot ne sont pas présentés comme terminés.

Les résultats de chaque révision sont consultables dans
[GitHub Actions](https://github.com/ronanmaudet-sketch/mailleur/actions).
La validation locale ci-dessus est identifiée comme une exécution Mac ;
les résultats Windows doivent être lus dans les jobs Windows correspondants.
Les contrats SOLIDWORKS utilisent des doubles de test et n’attestent aucune
conversion réelle par SOLIDWORKS ou exécution d’un solveur propriétaire.

## Historique conservé

La première construction publique, qui remappait seulement les chemins,
avait passé 200 tests Python en 34,787 s et l’exemple Cast3M en 0,4858 s.
Ces résultats restent conservés sous `initial_validation` dans
[summary.json](summary.json), associés à l’ancien SHA256. Les preuves rc7
du 7 novembre et les archives d’origine restent inchangées.

La [provenance du binaire](../../docs/provenance/BUILD_PUBLIC.json) et la
[note du correctif](../../docs/provenance/CORRECTIF_WINDOWS.md) précisent la
construction et les raisons de la modification.
