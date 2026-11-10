# Preuves Cast3M sélectionnées pour la publication

Cette sélection provient du dernier rejeu **du 7 novembre 2026** avec Cast3M
2026.0 arm64 et le noyau rc7 référencé dans le rapport. La préparation publique
du 10 novembre n’est pas une nouvelle exécution : **5 cas, 193 contrôles réussis**.

- [RAPPORT_CAST3M_RC7.md](RAPPORT_CAST3M_RC7.md) est une copie exacte du rapport final d’origine.
- [summary.json](summary.json) conserve les résultats, durées, seuils, retours,
  avertissements et empreintes ; seuls d’éventuels chemins personnels sont expurgés.
- Les cinq scripts `validation.dgibi` sont identiques aux scripts exécutés.
- Les cinq journaux `cast3m.log` sont des copies expurgées : préfixe local
  remplacé par `<workspace>` et utilisateur par `<user>`. Les lignes numériques
  `MAILLEUR_`, le niveau d’erreur et l’avertissement `IEEE_INVALID_FLAG` sont conservés.

| Cas final | Durée mesurée (arrondie à 6 décimales) | Script exécuté | Sortie conservée |
| --- | --- | --- | --- |
| `plan_p1_mm` | 1.016007 s | [script](plan_p1_mm/exports/validation.dgibi) | [journal expurgé](plan_p1_mm/exports/cast3m.log) |
| `plan_p2_mm` | 0.870753 s | [script](plan_p2_mm/exports/validation.dgibi) | [journal expurgé](plan_p2_mm/exports/cast3m.log) |
| `volume_p1_mm` | 0.756340 s | [script](volume_p1_mm/exports/validation.dgibi) | [journal expurgé](volume_p1_mm/exports/cast3m.log) |
| `volume_p2_mm` | 0.870184 s | [script](volume_p2_mm/exports/validation.dgibi) | [journal expurgé](volume_p2_mm/exports/cast3m.log) |
| `volume_p2_m` | 0.818773 s | [script](volume_p2_m/exports/validation.dgibi) | [journal expurgé](volume_p2_m/exports/cast3m.log) |

Les UNV et tous les intermédiaires de ces cinq cas ne sont pas recopiés ici.
Les mentions de dossiers complets dans le rapport décrivent la campagne
d’origine, fournie dans l’[archive externe](../../provenance/PREUVES_EXTERNES.md).
Ces scripts isolés ne sont donc pas annoncés comme cinq exemples autonomes.
Un exemple reproductible distinct est proposé dans
[`examples/cast3m_pilot`](../../../examples/cast3m_pilot/README.md).

Les SHA256 avant et après expurgation sont dans la
[provenance de publication](../../provenance/README.md). Aucun exécutable
Cast3M, bibliothèque, licence propriétaire ou donnée industrielle n’est joint.
