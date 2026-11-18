# Vérification des exports quadrilatères/hexaèdres — 18 novembre 2026

Les essais ci-dessous utilisent le noyau release `mailleur-core 1.0.0-rc.9`,
SHA-256 `537eb405731ed53efc43ff07a52a20004ba5e19d7aa9d8c176937fafca3ddfd2`.
Ils exécutent les solveurs installés sur le Mac Apple Silicon de préparation.
Les tolérances analytiques et critères de qualité restent ceux des bancs ;
aucun échec n'est converti en réussite ou ignoré.

| Vérification | Résultat réel |
| --- | --- |
| Cast3M 2026.0 | 5 cas réussis, 193 comparaisons : surfaces QUA4/QUA8, volumes CUB8/CU20, unités mm/m |
| CalculiX 2.23 | 8 cas réussis : CPS4/CPS8 et C3D8/C3D20, chacun en mm et m |
| Exemple public généré | 43 comparaisons réussies, export inchangé après calcul |
| Réexécution de la référence avec Python standard | 43 comparaisons réussies sans appel au paquet Mailleur |

Les contrôles Cast3M comparent les types, nombres de nœuds et d'éléments,
groupes d'éléments et de nœuds, bornes, déplacements, contraintes et forces à
la source ou à la solution de traction analytique. Le niveau d'erreur final
est zéro pour les cinq cas. CalculiX compare également les champs et réactions
à la traction analytique. Une exécution CalculiX ne certifie pas Abaqus.

Le pilote comporte 24 148 nœuds, 5 096 CU20, 1 902 QUA8 de frontière et 152
SEG3 de bord. La mémoire Cast3M est explicitement fixée à **1 024 Mio** et
reste configurable par `--memory-mib`. Avec 512 Mio, l'import de ce même
maillage réussissait mais le calcul épuisait la mémoire GEMAT ; cet essai
diagnostique n'est pas compté comme une réussite. Les volumes subdivisés et
quadratiques peuvent demander davantage de mémoire selon leur taille.

L'option réellement transmise au lanceur est `-MEM 1024Mo`. Le gestionnaire
Cast3M convertit `MO` en mots par `(LESO*1024/LBASE)*1024`, avec `LBASE=8`
sur cette version 64 bits : son `Mo` correspond donc ici à un Mio. Cette
conversion figure dans [la source officielle ooomop](https://www-cast3m.cea.fr/index.php?page=sources&source=ooomop).

- [Résultats Cast3M détaillés](solver_cast3m.json) et [marqueurs publics](solver_cast3m_markers.log).
- [Résultats CalculiX détaillés](solver_calculix.json).
- [Pilote et réexécution autonome](solver_reference.json).
- [Référence quadrilatères/hexaèdres](../../examples/cast3m_pilot/reference_quad_hex).
- [Règle des éléments et limites](../../docs/QUADRILATERES_HEXAEDRES.md).

Les en-têtes personnels et chemins locaux des journaux complets ne sont pas
publiés. Les résumés conservent les versions, l'empreinte du noyau et des
preuves brutes, les mesures effectives, budgets et tolérances. Les anciens
rapports rc7 et le dossier `reference/` du pilote restent des preuves
historiques triangulaires/tétraédriques ; la référence utilisée par la rc9
est `reference_quad_hex/`.

Ces essais couvrent la mécanique élastique de ces éprouvettes. Ils ne
certifient pas toutes les géométries, versions 2020+, physiques, contacts,
coques ou comportements non linéaires, ni les solveurs commerciaux absents.
