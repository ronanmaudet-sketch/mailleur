# Vérification réelle dans Cast3M — 7 novembre 2026

Les cinq cas passent : import UNV exécuté par Cast3M depuis le pont Gibiane exporté par Mailleur, puis calcul mécanique élastique et comparaison à une solution analytique. 193 contrôles numériques passent. Il ne s’agit pas d’une simple relecture par meshio.

## Environnement réellement exécuté

- Mailleur Python `1.0.0rc7`, noyau `mailleur-core 1.0.0-rc.7`, Python `3.12.16`.
- macOS 27.2, Darwin 27.2.0, machine arm64.
- Cast3M 2026.0, lancement avec le logiciel déjà installé dans `/Applications/Cast3M`.
- Noyau final avec correction des boîtes OpenCASCADE : SHA-256 `39fde2270d7282ebe460591308edebd6d7a978601e519fdddf441ee8d9d9cd85`.
- Exécutable Cast3M Mach-O **arm64 natif**, sans traduction Rosetta. Les empreintes SHA-256 du binaire et du lanceur figurent dans `summary.json`.
- Commande : `castem26 -MEM 512Mo -NCPU 1 -NOSWAP validation.dgibi` ; aucun changement de l’installation Cast3M.

## Cas et résultats

| Cas | Nœuds | Éléments par type (bords inclus) | Groupes | Temps Cast3M | Erreur maximale UX |
|---|---:|---|---:|---:|---:|
| plan_p1_mm | 25 | SEG2 : 16, TRI3 : 32 | 8 | 1.016 s | 1.6263e-19 mm |
| plan_p2_mm | 81 | SEG3 : 16, TRI6 : 32 | 8 | 0.871 s | 6.5052e-19 mm |
| volume_p1_mm | 94 | SEG2 : 44, TET4 : 221, TRI3 : 178 | 11 | 0.756 s | 1.3552e-19 mm |
| volume_p2_mm | 497 | SEG3 : 44, TE10 : 221, TRI6 : 178 | 11 | 0.870 s | 2.1684e-19 mm |
| volume_p2_m | 497 | SEG3 : 44, TE10 : 221, TRI6 : 178 | 11 | 0.819 s | 3.3087e-22 m |

Les temps couvrent le lancement de Cast3M, la lecture UNV, les contrôles et la résolution ; ils excluent la génération du maillage et son export. Ce sont des mesures ponctuelles de petits cas, avec d’autres vérifications actives sur la machine, pas une prévision de temps pour une pièce industrielle.

Les nombres de nœuds et d’éléments, les types linéaires/quadratiques, les effectifs des groupes d’éléments et de leurs groupes nodaux `_GN`, et les bornes géométriques ont été comparés dans Cast3M aux données du maillage source. Les noms de groupes sont utilisés directement dans les scripts et leur absence ferait échouer le calcul. Les fichiers exportés ont conservé exactement leurs empreintes après passage dans Cast3M.

## Calcul analytique

- Géométrie : plaque 2 × 1 mm en contraintes planes (épaisseur unitaire de 1 mm) ; bloc 2 × 1 × 1 mm en 3D.
- Élasticité isotrope : E = 210 000 N/mm², ν = 0,3 ; traction uniforme de 10 N/mm² sur x = L.
- Conditions : UX = 0 sur x = 0 ; UY = 0 sur y = 0 ; UZ = 0 sur z = 0 pour le volume. Les autres faces sont libres, avec contraction de Poisson.
- Référence : UX = traction × x / E ; UY = −ν × traction × y / E ; UZ = −ν × traction × z / E en volume.
- Déplacement axial maximal : 9,5238095238 × 10⁻⁵ mm ; contraction transversale maximale en valeur absolue : 1,4285714286 × 10⁻⁵ mm.
- Contrainte axiale uniforme : 10 N/mm². Force totale appliquée : 10 N ; réaction sur x = 0 : −10 N.
- Le cas `volume_p2_m` exporte le même bloc en mètres : dimensions 0,002 × 0,001 × 0,001 m, E = 210 × 10⁹ Pa et traction = 10 × 10⁶ Pa. Le déplacement axial devient 9,5238095238 × 10⁻⁸ m ; l’effort total reste 10 N.

Le banc vérifie les erreurs des champs UX/UY/UZ sur tous les nœuds dans Cast3M (limite : 10⁻⁸ du déplacement axial de référence). Il compare aussi les minima et maxima de contrainte et l’équilibre des efforts. Les valeurs imprimées par `MESS` ont six chiffres significatifs ; les comparaisons de valeurs affichées utilisent une tolérance relative de 2 × 10⁻⁶.

## Limites et particularités constatées

- Le lecteur `LIRE UNV` peut réinitialiser l’option de calcul : le validateur rétablit `OPTI MODE PLAN CONT` après l’import pour les cas plans. Le pont exporté laisse volontairement au destinataire la définition de son modèle mécanique.
- Les journaux se terminent avec un avertissement Fortran `IEEE_INVALID_FLAG`, également présent sur le programme minimal de référence sans import ni calcul (dossier `probe`). Dans les cinq calculs, le code de retour est 0, le niveau d’erreur Cast3M est 0, et toutes les comparaisons passent. Cet avertissement reste consigné sans être masqué.
- Cette preuve couvre Cast3M 2026.0 sur cette machine, les éléments SEG2/SEG3, TRI3/TRI6 et TET4/TE10, ces groupes et ces unités. Elle ne certifie pas toutes les versions 2020+ ni les maillages hexaédriques, coques, non-linéarités, contacts ou matériaux industriels.
- Aucun logiciel solveur tiers autre que ce Cast3M n’est attesté par ce rapport.

## Reproduire et consulter les preuves

Depuis la racine du projet :

```sh
.venv/bin/python scripts/validate_cast3m.py
```

Chaque dossier de cas contient `project.json`, le résultat de génération, `exports/import_cast3m.dgibi`, le fichier UNV et ses métadonnées, `exports/validation.dgibi`, `exports/cast3m.log` et `result.json`. Le fichier `summary.json` regroupe les résultats et les empreintes.

Les commandes suivent les notices primaires installées dans `/Applications/Cast3M/notice/` (`lire`, `nbno`, `nbel`, `opti`, `mode`, `mate`, `rigi`, `bloq`, `fsur`, `reso`, `sigm`, `reac`). Les correspondants en ligne sont notamment [LIRE](https://www-cast3m.cea.fr/index.php?notice=LIRE&page=notices_classees), [FSUR](https://www-cast3m.cea.fr/index.php?notice=FSUR&page=notices_classees), [RESO](https://www-cast3m.cea.fr/index.php?notice=RESO&page=notices_classees) et [SIGM](https://www-cast3m.cea.fr/index.php?notice=SIGM&page=notices_classees).
