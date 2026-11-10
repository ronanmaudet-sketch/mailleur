# Provenance des documents publics

Cette copie de publication a été préparée le **10 novembre 2026** à partir
de l’archive légère rc7 :

- `Mailleur_rc7_sources_GitHub.zip` — SHA256
  `51072fe062c8a18805177f74800b55eb0e193d62f1e9633638db8a5794ed292a`.
- Archive complète d’origine : `Mailleur_rc7_verifie_2026-11-07.zip` — SHA256
  `d1a1bcc758242c0c858b4c7456790cd4125a0ec7574c18acdac2acaaab3eac1f`.

Les archives d’origine restent inchangées. Les sources de calcul et les
licences ne sont pas réécrites par cette opération documentaire. La provenance
du binaire public, reconstruit séparément si nécessaire, doit être lue dans
[sa notice de construction](BUILD_PUBLIC.json) : les empreintes de noyau figurant dans les résultats
historiques désignent toujours le noyau effectivement testé à cette date.

## Copies expurgées

Le [relevé JSON](expurgations.json) indique chaque fichier source relatif,
son SHA256 original, le chemin public et son SHA256 après préparation.
Les chemins de travail personnels sont remplacés par `<workspace>`, les
préfixes temporaires par `<temporary>` et le nom d’utilisateur des bandeaux
par `<user>`. Les noms relatifs de fichiers peuvent rester comme traces
d’exécution ; ils ne doivent pas être pris pour des chemins disponibles chez le lecteur.

Pour les résumés JSON, la structure, tous les nombres, booléens et valeurs
nulles ont été comparés avant/après et sont identiques. Aucune durée, erreur,
acceptation, avertissement ou quantité de test n’a été changée. Dans les
journaux Cast3M, les lignes de résultats `MAILLEUR_` sont également identiques.
Le rapport final et les cinq scripts Gibiane sont copiés sans modification.

Les PDF/Word historiques ne sont pas régénérés. Leurs mesures ou formulations
anciennes sont complétées par l’[erratum](../../ERRATUM_PUBLICATION.md).
Les rapports publiés sont des preuves d’exécutions antérieures, jamais une
nouvelle campagne exécutée lors de la préparation du dépôt.

## Liens et données externes

La [notice de distribution externe](PREUVES_EXTERNES.md) explique les fichiers
non inclus et leur provenance. Le manifeste `MANIFEST_RC7_ORIGINE.sha256`
conserve la référence historique de la base ; les modifications documentaires
et ajouts publics doivent figurer dans un manifeste de publication distinct.
