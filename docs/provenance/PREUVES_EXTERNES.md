# Preuves externes à la distribution publique légère

Le dépôt GitHub préparé à partir de la rc7 contient le code, ses tests, les
exemples, les notices et une sélection de rapports. Il **ne contient pas**
les corpus complets, les maillages intermédiaires, tous les journaux de calcul
ni les anciennes sources rc4 utilisées pour les comparaisons de performance.

## Archive complète historique

Nom : `Mailleur_rc7_verifie_2026-11-07.zip`  
Taille : **96 713 895 octets**  
SHA256 : `d1a1bcc758242c0c858b4c7456790cd4125a0ec7574c18acdac2acaaab3eac1f`

Cette archive est conservée séparément du dépôt et doit être obtenue auprès
de son détenteur ; aucun lien de téléchargement public n’est inventé ici.
Les chemins `verification/...` mentionnés dans les documents historiques
sont relatifs au dossier `atelier_mailleur` de cette archive. Leur absence du
dépôt ne constitue ni une nouvelle réussite de test, ni une perte d’une
fonction du programme.

Les comparaisons rc4 nécessitent en particulier
`verification/optimization/reproduction/rust_rc4` et leurs données d’entrée.
Les commandes du guide d’optimisation supposent que ces fichiers externes
ont été obtenus ; elles ne sont pas directement exécutables sur le seul dépôt léger.

## Documents omis référencés dans les guides

Les liens historiques absents ont été dirigés vers cette notice plutôt que
vers un fichier inexistant. Les cibles d’origine étaient notamment :

- `../verification/cli_rc4/plan.json`
- `../verification/cli_rc4/run.json`
- `../verification/cli_rc4/status.json`
- `../verification/cli_rc4/train.json`
- `../verification/continuous_learning/summary.json`
- `../verification/corpus/summary.json`
- `../verification/optimization/archive_smoke.json`
- `../verification/optimization/bulk.json`
- `../verification/optimization/corpus_resume.json`
- `../verification/optimization/live.json`
- `../verification/optimization/live_compatibility.json`
- `../verification/optimization/live_storage.json`
- `../verification/optimization/live_warm.json`
- `../verification/optimization/quality.json`
- `../verification/optimization/training.json`
- `../verification/optimization/transport.json`
- `../verification/optimization/viewport.json`
- `bounds_regressions.txt`
- `cast3m/preuves/README.md`
- `occ_scaled_sphere_probe.json`
- `run-20261107T185138766837/summary.json`
- `run-20261107T185415451486/summary.json`
- `run-20261107T190216151961/summary.json`
- `verification/continuous_learning/summary.json`
- `verification/corpus/summary.json`


## Preuves publiques disponibles

- [Résumé étendu expurgé](../../verification/extended_20261107/summary.json).
- [Rapport et cinq jeux de scripts/journaux Cast3M](../cast3m/preuves/README.md).
- [Rapport natif historique](../../verification/native_release/summary.json).
- [Provenance et empreintes des copies](README.md).
- [Erratum de publication](../../ERRATUM_PUBLICATION.md).

Les chiffres de ces rapports gardent leurs dates et leur environnement
d’exécution. Publier ces fichiers le 10 novembre n’exécute aucun calcul de plus.
