# Vérifications du moteur direct sur Mac M3

La rc10 provient de `Atelier_Mailleur_Rust_Python_rc6.zip` (47 740 610 octets). L’empreinte SHA-256 du ZIP est `fcbed3095480810a58fc0d1c950c3a1e2afd1a9ccd55197fc641991173e4a54b`. Les 1 878 fichiers du manifeste d’origine ont été contrôlés avant intégration. Les résultats ci-dessous ont été produits par le moteur direct sur macOS arm64 le 19 novembre 2026.

| Cas automatique | Éléments du domaine | Nœuds du maillage | Temps mesuré par l’API* |
| --- | ---: | ---: | ---: |
| Plaque, ordre 1 | 48 QUAD4 | 63 | 57 ms |
| Plaque, ordre 2 par défaut | 48 QUAD8 | 173 | 53 ms |
| Bloc, ordre 1 | 72 HEX8 | 140 | 64 ms |
| Bloc, ordre 2 par défaut | 72 HEX20 | 477 | 80 ms |

\* Un appel de génération sur ce Mac par ligne, y compris le lancement du processus natif. Le temps de génération dans le noyau et les valeurs non arrondies figurent dans le relevé. La relecture a contrôlé les types et le nombre d’éléments ; le rapport du noyau identifie la construction directe des cellules et des nœuds quadratiques. [Relevé des quatre cas](maillages_mac.json) · [Versions et suites de tests](tests_mac_arm64.json)

Pour reproduire ces quatre cas depuis le dossier installé, exécuter `python scripts/check_native_smoke.py --output output/controle_direct`. Pour l’apprentissage, le pilote simule 32 configurations en six familles avec 96 essais de réglages. La liste des pièces, les résultats et le modèle partageable sont disponibles dans le [dossier du pilote](../direct_quad_hex/corpus/README.md).

## Calcul indépendant dans Cast3M

Une traction élastique sur un bloc de 2 × 1 × 1 mm a été calculée dans Cast3M 2026.0 après export UNV du maillage direct de 16 HEX20 et 141 nœuds. Les 43 comparaisons des groupes, des déplacements, de la contrainte et de l’équilibre ont réussi. Ce cas couvre précisément cette éprouvette et cette version du solveur. [Rejouer le cas](../../examples/cast3m_pilot/README.md) · [Détail des comparaisons](resultat_cast3m.json) · [Extrait du journal](cast3m_extrait.log)

Le maillage entièrement hexaédrique d’une CAO quelconque demanderait encore un découpage automatique en blocs. Les essais de l’ancienne rc9 sur des pièces STEP gardent leur révision d’origine. [Historique rc9](https://github.com/ronanmaudet-sketch/mailleur/tree/v1.0.0-rc9)
