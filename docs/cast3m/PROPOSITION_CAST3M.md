## Mailleur pour les utilisateurs de Cast3M

**Note de présentation et proposition de pilote — 10 novembre 2026**

Projet Mailleur · Version technique étudiée : 1.0.0rc7 · Destinataires : équipe de développement et utilisateurs de Cast3M.

### Une proposition à évaluer

Mailleur est un outil local de préparation géométrique, de génération et de contrôle des maillages. Cette note propose d'évaluer son intérêt comme complément au parcours de préparation d'une étude Cast3M : retrouver les réglages employés, contrôler les unités et les éléments, exporter les groupes et rejouer les essais.

Le potentiel d'optimisation concerne d'abord le travail de préparation et les essais de maillage. **Aucun gain sur le temps de résolution du solveur Cast3M n'a été démontré.** Les gains de temps opérateur et l'intérêt de l'apprentissage sur des pièces industrielles restent à mesurer.

### Ce qui existe déjà

- Une interface Python/Qt, des commandes et une API HTTP locale, avec un noyau numérique Rust pilotant Gmsh/OpenCASCADE.
- Des projets décrits en JSON, des maillages surfaciques ou tétraédriques linéaires et quadratiques, des rapports de qualité et des refus explicites.
- Un export UNV accompagné d'un script Gibiane, essayé par de vrais calculs dans Cast3M 2026.0 sur Mac arm64.
- Le code, des exemples et des tests sous GPL version 3 ou ultérieure ; les dépendances conservent leurs licences.

### Apport proposé

| Besoin | Apport possible de Mailleur | À démontrer pendant le pilote |
| --- | --- | --- |
| Rejouer une préparation | Projet, unités, paramètres et rapports conservés | Reproductibilité sur les cas de l'équipe |
| Comparer des recettes | Essais bornés et décision de qualité visible | Temps total et taux de maillages exploitables |
| Échanger avec Cast3M | UNV, groupes et script d'import | Interprétation des groupes sur des pièces réelles |
| Évaluer une assistance apprise | Classement expérimental de recettes | Gain sur géométries indépendantes, à coût comparable |

La lecture UNV est déjà documentée par Cast3M [1]. L'apport présenté porte donc sur l'orchestration, la traçabilité et les contrôles du parcours, plutôt que sur l'invention d'un nouveau format d'échange.

**Proposition à l'équipe :** examiner un démonstrateur reproductible, choisir un petit jeu de pièces de référence et définir ensemble les critères permettant de décider si cette approche mérite une intégration plus poussée.

<!-- PAGEBREAK -->

## 1. Architecture et place dans une étude Cast3M

Le parcours peut d'abord rester extérieur à Cast3M, en utilisant son interface d'import existante.

```text
CAO / projet JSON -> Mailleur (CLI, Qt ou API locale)
  -> Rust + Gmsh/OpenCASCADE -> controles et rapport
  -> fichier UNV + script Gibiane -> LIRE 'UNV'
  -> modele physique et calcul dans Cast3M
```

| Composant | Responsabilité |
| --- | --- |
| Python : interface et NativeSession | Saisie, orchestration, fichiers, délais, lancement des opérations et présentation des résultats |
| Rust + Gmsh/OpenCASCADE | Géométrie, génération, métriques numériques et contrôle de qualité |
| Écrivain UNV + pont Gibiane | Transport du maillage, des types d'éléments et des groupes pris en charge |
| Cast3M | Lecture, formulation mécanique, matériaux, conditions limites, chargements et résolution |
| Guides appris expérimentaux | Classement de recettes à essayer ; ils ne fabriquent pas directement les éléments et ne décident pas seuls de leur acceptation |

### Contrat d'échange initial

Le pont exporté demande la lecture du fichier UNV puis donne accès au maillage et à sa table de groupes. Le principe de lecture est celui de la notice officielle [1] :

```text
TAB1 = LIRE 'UNV' 'import_cast3m.unv' ;
MAIL1 = TAB1.'MAILLAGE' ;
```

Dans l'export, la géométrie interne est exprimée en mètres ; l'unité d'export est choisie explicitement et consignée. Le jeu de calcul doit employer des unités cohérentes pour les dimensions, E, les forces et les pressions. Les données d'unité UNV ne doivent pas être assimilées à une conversion automatique du modèle physique dans Cast3M.

Les matériaux, sections, contacts, chargements et conditions limites restent à définir dans Cast3M. Le démonstrateur les ajoute explicitement pour une éprouvette. Les groupes exportés ne représentent pas automatiquement une sémantique mécanique complète.

### Une intégration progressive

La première étape proposée est un lanceur local ou un exemple Gibiane documenté, sans modification du solveur. Une automatisation par la CLI ou NativeSession peut ensuite s'insérer dans les outils de l'équipe. L'API HTTP actuelle est conçue pour un usage local ; une exposition multi-utilisateur demanderait un travail spécifique.

Une boucle d'adaptation à partir des erreurs estimées par Cast3M serait un développement ultérieur : échange de champs, localisation des erreurs, conservation des groupes, projection et étude de convergence restent à concevoir et valider.

<!-- PAGEBREAK -->

## 2. Preuves disponibles et périmètre réel

La campagne locale du 7 novembre 2026 a exécuté cinq calculs dans **Cast3M 2026.0, arm64 natif**, sur un Mac Apple M3 doté de 16 Go de mémoire. Le noyau Mailleur rc7 utilisé est identifié par l'empreinte 39fde227...d9cd85 ; l'empreinte complète est conservée dans les preuves livrées.

| Cas | Nœuds | Éléments du domaine | Groupes | Temps Cast3M |
| --- | ---: | --- | ---: | ---: |
| Plan P1, mm | 25 | 32 TRI3 | 8 | 1,016 s |
| Plan P2, mm | 81 | 32 TRI6 | 8 | 0,871 s |
| Volume P1, mm | 94 | 221 TET4 | 11 | 0,756 s |
| Volume P2, mm | 497 | 221 TE10 | 11 | 0,870 s |
| Volume P2, m | 497 | 221 TE10 | 11 | 0,819 s |

Les éléments de bord s'ajoutent à ces nombres. **193 comparaisons** passent sur les cinq cas : effectifs, types, groupes et groupes nodaux associés, dimensions, déplacements, contraintes et équilibre des efforts. Il ne s'agit pas d'une vérification exhaustive de toutes les appartenances élémentaires des groupes.

Les temps incluent le lancement de Cast3M, la lecture UNV, les contrôles et la résolution. Ils excluent la génération et l'export du maillage. Ils sont ponctuels, sans référence concurrente, et ne constituent pas une mesure d'accélération de Cast3M.

### Référence mécanique

L'éprouvette 3D mesure 2 × 1 × 1 mm. Elle est élastique isotrope, avec E = 210 000 N/mm² et nu = 0,3, sous traction uniforme p = 10 N/mm² et contraction latérale libre. La solution attendue est ux = p x / E, uy = -nu p y / E et uz = -nu p z / E. Le déplacement axial maximal vaut environ 9,52381e-5 mm et l'effort résultant 10 N.

Les erreurs de déplacement extrêmement faibles observées sur ce champ affine ne prouvent pas une précision industrielle générale. Les seuils de comparaison et les sorties brutes sont conservés dans le rapport Cast3M fourni.

### Limites à retenir

- Ces essais couvrent cette version et cette machine, des triangles et tétraèdres P1/P2 et une mécanique linéaire simple ; pas toutes les versions 2020+, les coques, contacts ou non-linéarités.
- Le lecteur UNV peut réinitialiser des options de calcul ; le banc rétablit le mode de contraintes planes après import pour les cas plans.
- Un avertissement Fortran IEEE_INVALID_FLAG apparaît aussi sur une sonde minimale Cast3M ; il est conservé. Les calculs ont un code de retour et un niveau d'erreur nuls.
- Les bornes CAO de Mailleur peuvent être approchées par tessellation. Qualité géométrique, conformité des groupes et précision de la solution sont des contrôles distincts.

<!-- PAGEBREAK -->

## 3. Pilote comparatif proposé

Le pilote servirait à vérifier une hypothèse simple : peut-on réduire le temps de préparation et le nombre d'essais manuels, tout en respectant les critères mécaniques de l'équipe ? Son périmètre et ses seuils seraient arrêtés avant les mesures.

### Jeu de cas à convenir

Un premier lot pourrait comporter 8 à 12 pièces partageables : éprouvettes analytiques, courbures, perçages, variations d'épaisseur, zones de concentration et quelques cas volontairement difficiles. Il devrait inclure les groupes, unités et formulations effectivement utilisés par les destinataires. Ce lot est une proposition ; il n'a pas déjà été validé sur des pièces de l'équipe.

### Comparaison à mener

| Mesure séparée | Questions à examiner |
| --- | --- |
| Temps opérateur | Combien de réglages, reprises et corrections sont nécessaires ? |
| Génération et contrôle | Quels temps, pics de mémoire, éléments refusés et motifs d'échec ? |
| Export et import Cast3M | Les unités, types et appartenances des groupes sont-ils corrects ? |
| Assemblage et résolution | À précision mécanique comparable, quelle taille de problème et quel temps ? |
| Résultats physiques | Les références, réactions et études de convergence sont-elles respectées ? |
| Répétabilité | Les paramètres, versions, ressources et variations numériques sont-ils documentés ? |

Le parcours habituel de l'équipe constitue la référence. Les ressources et réglages sont enregistrés ; plusieurs répétitions sont effectuées. Les temps de préparation, maillage, transfert et calcul sont publiés séparément. Une réduction du nombre d'éléments n'est pas un gain acceptable si la précision convenue est dégradée.

### Conditions pour conclure positivement

- Aucune perte silencieuse de nœuds d'ordre supérieur, de groupes ou d'unités dans le périmètre retenu.
- Contrôle des appartenances des groupes, au-delà des seuls effectifs testés actuellement.
- Résultats mécaniques et convergence conformes à des tolérances convenues, avec refus explicites des cas non pris en charge.
- Gain observable et répétable sur au moins une mesure utile, en comptant le coût des essais et des échecs.
- Si un guide appris est évalué : séparation des géométries d'apprentissage et d'évaluation, comparaison au moteur classique et comptabilisation du coût d'apprentissage.

Livrables proposés : exemples rejouables, versions et paramètres, résultats bruts, mesures par phase, anomalies constatées et une conclusion sur l'intérêt ou les limites de l'approche. Aucun calendrier ni engagement de l'équipe Cast3M n'est supposé dans cette note.

<!-- PAGEBREAK -->

## 4. Essayer le démonstrateur et discuter la suite

Le dossier de publication contient le code rc7, ses licences, le guide général et un exemple de traction avec un maillage tétraédrique quadratique. L'exemple est une entrée simple pour examiner l'interface d'échange, sans prétendre représenter une étude industrielle.

### Démarrage depuis le dépôt

Python 3.11 à 3.13 est requis. Le noyau Mac arm64 est fourni dans la distribution préparée ; Rust/Cargo est nécessaire pour le reconstruire ou pour une autre plateforme. Cast3M doit être installé séparément selon ses propres conditions d'utilisation.

```sh
python scripts/install.py
.venv/bin/python examples/cast3m_pilot/run_example.py --help
.venv/bin/python examples/cast3m_pilot/run_example.py \
  --castem /chemin/vers/castem26 \
  --output output/cast3m_pilot
```

Sur Windows, adapter le chemin de Python en .venv\Scripts\python.exe ; cette nouvelle intégration n'a pas été exécutée sur Windows. Le README de l'exemple précise les commandes, fichiers, hypothèses et résultats de la réexécution du 10 novembre.

Les sources de référence des cinq cas se trouvent dans scripts/validate_cast3m.py. Le dossier docs/cast3m/preuves/ fournit le rapport final, le résumé et les sorties sélectionnées ; les anciens relevés restent datés et ne sont pas renommés comme de nouvelles performances.

### Licence et collaboration

Le code propre à Mailleur est fourni sous GPL-3.0-or-later. Les dépendances et Cast3M conservent leurs licences ; aucun binaire Cast3M ou traducteur propriétaire n'est redistribué. Les conditions d'une éventuelle intégration ou distribution conjointe devront être examinées selon le périmètre choisi.

Dépôt GitHub : [https://github.com/ronanmaudet-sketch/mailleur](https://github.com/ronanmaudet-sketch/mailleur). Consulter le README et les commits pour connaître la version actuellement en ligne. Cette note est une proposition indépendante : elle n'implique aucun partenariat ni validation par l'équipe Cast3M.

### Points proposés à la discussion

Quelles étapes du parcours actuel sont les plus coûteuses ? Quels groupes et formulations faut-il absolument conserver ? Quelles pièces et tolérances permettraient une comparaison utile ? Un premier échange pourrait choisir un cas de référence et convenir d'un protocole avant tout développement supplémentaire.

### Références et preuves

[1] Cast3M, notice officielle LIRE, partie UNV : https://www-cast3m.cea.fr/index.php?notice=LIRE&page=notices_classees (consultée le 10 novembre 2026).

[2] Cast3M, présentation et périmètres : https://www-cast3m.cea.fr/index.php?xml=presentation (consultée le 10 novembre 2026).

[3] Mailleur rc7, rapport et résumé Cast3M : docs/cast3m/preuves/RAPPORT_CAST3M_RC7.md et summary.json, campagne du 7 novembre 2026.

[4] Exemple public : examples/cast3m_pilot/README.md et résultats de référence de la réexécution locale du 10 novembre 2026.
