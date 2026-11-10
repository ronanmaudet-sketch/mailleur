# Vérification étendue du noyau — 7 novembre 2026

Le bilan exploitable est [final_consolidated.json](final_consolidated.json). Les requêtes, sorties, codes de retour et rapports détaillés restent dans les dossiers `run-*`. Les campagnes rc6 ayant révélé des défauts sont conservées séparément ; leurs échecs ne sont pas comptés comme des réussites rc7.

Le dernier noyau macOS ARM64 testé porte la version `1.0.0-rc.7`, SHA-256 `39fde2270d7282ebe460591308edebd6d7a978601e519fdddf441ee8d9d9cd85`. Machine : Apple M3, 16 Go de mémoire. Les durées sont des observations en présence d'autres vérifications, sans conclusion de gain de vitesse entre campagnes.

## Maillages et unités

Campagne finale [run-20261107T190216151961/summary.json (archive complète)](../../../docs/provenance/PREUVES_EXTERNES.md), durée totale de ses sections : 120,722 s.

| Résultat des 288 opérations réelles | Nombre |
| --- | ---: |
| Maillage accepté | 257 |
| Refus de la politique de qualité | 12 |
| Refus pour éléments inversés ou dégénérés | 3 |
| Refus structuré de l'optimiseur quadratique Gmsh | 4 |
| Géométrie volontairement invalide refusée | 12 |
| Crash, dépassement du délai ou invariant échoué | 0 |

La matrice couvre 24 familles, trois graines (`7`, `42`, `2026`), ordres 1/2 et unités mm/m, modes plan/surface/volume. Les 257 acceptations passent les contrôles natifs complets : nombres finis, Jacobiennes et qualité positives, comptage exhaustif des éléments. Les refus gardent leur requête reproductible et le motif exact.

Sur les 131 paires mm/m produisant deux rapports de qualité, les dimensions physiques et les tailles de maille concordent dans les limites déclarées des boîtes tessellées. Les décisions de qualité concordent dans 127 paires. Quatre paires changent de décision : les arrondis de conversion peuvent changer un maillage non structuré et donc sa qualité. Aucune de ces 131 paires ne produit exactement les mêmes nombres de nœuds/éléments et qualités. En revanche, huit répétitions avec le même projet et la même graine produisent exactement la même signature de rapport sur cette installation.

La campagne a révélé des boîtes OpenCASCADE périmées après dilatation d'une sphère : une arête annonçait encore ±2,9 **m** pour un rayon physique de 2,9 **mm**, alors que les nœuds étaient déjà correctement en SI. Le réglage officiel `Geometry.OCCBoundsUseStl=1`, appliqué avant toute construction/import/synchronisation, corrige les boîtes utilisées par l'inspection et la représentation. Les rapports précisent désormais leur origine tessellée et leur approximation ; ils ne revendiquent pas une enveloppe analytique exacte. Les extrema d'une surface courbe peuvent être légèrement sous-estimés. La comparaison interunités emploie une tolérance de test de quatre déflexions relatives multipliées par l'étendue ; les tailles cibles restent comparées à la précision des flottants.

Les trois tests de `tests/test_extended_core.py` passent en 7,313 s : traitement structuré du défaut HighOrder et inspections/maillages/aperçus/exportations MED réelles de sphères en surface et volume, dans les deux unités. Voir [bounds_regressions.txt (archive complète)](../../../docs/provenance/PREUVES_EXTERNES.md) et la preuve indépendante [occ_scaled_sphere_probe.json (archive complète)](../../../docs/provenance/PREUVES_EXTERNES.md). L'optimiseur et les seuils de qualité sont conservés ; ses erreurs ne provoquent plus SIGABRT et aucun résultat refusé n'est publié comme maillage utilisable.

Les 49 vérifications de reconstruction comportent 41 acceptations et 8 refus attendus, tous vérifiés comme réponses JSON structurées avec code 2. Elles couvrent les connectivités linéaires/quadratiques, transformations affines avec miroirs, extraction de faces internes et mélange tétraèdres/hexaèdres. Les sources restent inchangées. Ces fixtures contrôlent la connectivité et les sommets primaires ; elles ne certifient pas les Jacobiennes courbes d'un fichier source ni toutes les intersections géométriques.

## Taille bornée et mémoire

Le gros cas final est une boîte de 20 × 16 × 8 mm, taille cible 0,42 mm, ordre 1, un fil : **166 247 tétraèdres, 31 321 nœuds, 2,577 s**, pic RSS du seul processus Rust **372 506 624 octets, soit 355,25 Mio**. Qualité minimale 0,269276 ; moyenne 0,811396. Ce cas respecte une borne demandée de 300 000 éléments ; il ne constitue pas une limite mémoire industrielle ou celle de l'interface Qt complète.

La capacité d'entraînement a été mesurée sur **100 000 enregistrements synthétiques**, deux actions et 128 entrées par cas, **aucun maillage réel généré**. Fixture : 133 726 670 octets (133,73 Mo, 127,53 Mio), créée en 2,438 s puis supprimée après conservation de son empreinte. Réseau 4 161 paramètres, deux époques : pause après sept mini-lots en 4,551 s ; reprise 22,231 s, pic RSS 4 751 360 octets (4,53 Mio) ; entraînement continu 22,281 s. Poids, validation, décision et métriques finales sont identiques entre reprise et exécution continue. Ce jeu volontairement simple mesure le streaming et la reprise ; il ne mesure pas la généralisation à des pièces industrielles. Source : [run-20261107T185138766837/summary.json (archive complète)](../../../docs/provenance/PREUVES_EXTERNES.md), noyau rc7 antérieur au seul changement de boîtes Gmsh.

## Corpus réel, reprise et intégrité

La planification d'un million de cas crée seulement trois fichiers, **5 273 octets**, zéro cas évalué et zéro maillage. Elle ne prouve pas qu'un million de cas ont été calculés.

Une campagne réelle distincte de **96 cas** (48 structurés, 48 aléatoires, 24 familles, deux stratégies) a été interrompue après trois lignes durables puis reprise : 96 indices contigus, 96 identifiants uniques. Modifier un octet d'un ancien segment fermé provoque désormais un refus sans changer le checkpoint ; après restauration, les 96 cas restent accessibles. L'entraînement vérifie les 13 segments fermés et le préfixe courant avant d'utiliser les données. Les familles d'entraînement/validation/test sont disjointes. Ce petit modèle ne dépasse pas sa référence de validation et reste désactivé pour le déploiement. Source : [run-20261107T185415451486/summary.json (archive complète)](../../../docs/provenance/PREUVES_EXTERNES.md).

Deux défauts supplémentaires ont ainsi été corrigés : contrôle des anciens segments via un ledger SHA-256 streaming, et lecture JSON `f64` correctement arrondie pour supprimer une dérive de deux ULP à la reprise d'un checkpoint. Les corpus rc6 exigent une adoption explicite de leur intégrité historique ; celle-ci reste signalée comme non prouvée. Les anciens contextes d'apprentissage restent conservés et distincts des contextes numériques rc7.

Ces vérifications bornées ne remplacent pas un calcul de référence indépendant, une étude de convergence, une validation mécanique ou les vérifications du solveur destinataire.
