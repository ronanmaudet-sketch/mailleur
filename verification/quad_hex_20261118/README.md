# Vérification quadrilatères/hexaèdres — 18 novembre 2026

Le noyau de livraison rc9 est identifié par son SHA-256 dans [summary.json](summary.json).
Les contrôles portent sur les fichiers effectivement écrits puis relus, et sur
les maillages finaux des véritables essais d'apprentissage.

- [Vérifications locales](local_checks.json) : 143 tests Rust et 207 tests Python
  réussis, sans échec ni test ignoré ; syntaxe JavaScript et quatre cas natifs vérifiés.
- [Imports directs](direct_imports.json) : 48 cas réussis (STEP, IGES, BREP et
  peaux de maillage, unités mm/m, ordres 1/2), sur le même noyau release.
- [Résultats des solveurs](solver_README.md) : cinq cas Cast3M (193 comparaisons),
  huit cas CalculiX et pilote Cast3M généré puis réexécuté (43 comparaisons chacun).
- [Contrat et limites](../../docs/QUADRILATERES_HEXAEDRES.md).

## Démonstrations sur le Mac de préparation

| Domaine | Ordre | Nœuds | Éléments du domaine | Appel complet Python | Qualité minimale |
| --- | ---: | ---: | ---: | ---: | ---: |
| Plan | 1 | 401 | 372 | 0.458 s | 0.593 |
| Plan | 2 | 1,173 | 372 | 0.060 s | 0.593 |
| Volume | 1 | 6,503 | 5,096 | 0.145 s | 0.195 |
| Volume | 2 | 24,148 | 5,096 | 0.436 s | 0.195 |

La plaque mesure 4 × 2 mm ; la boîte 2 × 1 × 1 mm, avec une taille cible de
0,65 mm. Les frontières sont comptées séparément dans le JSON. Chaque temps
est une mesure ponctuelle, pas un benchmark statistique ou une prédiction pour
une CAO industrielle. Le premier appel inclut le démarrage à froid ; les temps
natifs de géométrie, génération et validation sont distingués dans le rapport.

## Nouveaux modèles réellement entraînés

| Guide | Taille JSON | Paramètres ajustables | Activation |
| --- | ---: | ---: | --- |
| historique | 7,730 octets | 147 | Désactivé |
| experience | 48,680 octets | 1,561 | Désactivé |
| corpus | 125,632 octets | 4,161 | Désactivé |

Le guide historique compare quatre petites géométries (deux entraînement, deux
validation). La mémoire interactive contient deux contextes et quatre essais.
Le pilote de corpus exécute 24 cas : 21 ont produit un maillage et 18 possèdent
une stratégie admissible. Les 48 actions comprennent 41 réussites vérifiées
et sept échecs ; les familles sont séparées avant entraînement (16 cas train,
quatre validation, quatre test). Le nombre de calculs natifs peut dépasser le
nombre d'actions en raison des traitements internes ; le JSON rapporte 70
évaluations du backend.

Aucun de ces guides n'a démontré un gain suffisant pour être activé. Les poids
[historique](models/historique.json), [expérience](models/experience.json) et
[corpus](models/corpus.json) sont des démonstrations reproductibles, conservées
avec leur provenance et leurs empreintes. Ils ne sont pas installés comme
modèles actifs par défaut. Les anciens modèles sont conservés dans leurs
répertoires historiques et sont refusés par le nouveau contrat de partage.

Pour reproduire ces essais dans deux **nouveaux** dossiers :

```sh
python scripts/validate_quad_hex_learning.py --output output/essai_quad_hex --evidence output/preuves_quad_hex
python scripts/check_native_smoke.py --output output/controle_quad_hex
```

Le premier script laisse tous les diagnostics bruts dans le dossier de travail
local. Son dossier de preuve contient seulement les géométries synthétiques de
démonstration, les résumés et les poids ; aucune pièce personnelle n'est incluse.
Les essais synthétiques purement mathématiques du réseau sont distincts de ces
vrais maillages et ne créent jamais un guide déployable.
