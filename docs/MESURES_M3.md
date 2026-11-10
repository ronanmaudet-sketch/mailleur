# Mesures et installation sur le MacBook Pro M3

## Machine et méthode

Mesures du 4 novembre 2026 sur le MacBook Pro de l'utilisateur : **Apple M3, 8 cœurs CPU, GPU 10 cœurs, mémoire unifiée 16 Go, macOS 27.0, arm64**. L'espace disponible relevé au début était d'environ 797 Gio. Aucun numéro de série matériel n'est inclus dans les livrables.

Environnement isolé : Python **3.12.16**, Rust/Cargo **1.99.1**, Gmsh **4.15.2**, meshio **5.3.5**, h5py **3.16.0**, NumPy **2.5.4**, PySide6-Essentials et shiboken6 **6.12.0**. Les versions sont consignées dans `verification/local_m3/requirements-resolved.txt`. Les dépendances Cargo utilisent `Cargo.lock` et le profil release. Le GPU n'est pas utilisé par les réseaux actuels.

Les mesures de génération utilisent un seul thread demandé au mailleur, sans conseiller neuronal. Trois répétitions sont conservées ; la valeur annoncée est leur médiane. Le temps comprend la préparation de la requête, le démarrage du noyau Rust, le calcul, les fichiers et le chargement des rapports/aperçus par `NativeSession`. Les exports sont mesurés séparément. Ce protocole ne mesure pas l'ouverture de la fenêtre, un transfert HTTP ni un calcul mécanique dans Cast3M.

## Génération de maillages

| Cas | Nœuds | Éléments du domaine | Génération, médiane | Export MSH, médiane |
| --- | ---: | ---: | ---: | ---: |
| Plaque percée à congés, exemple 2D | 363 | 624 triangles | 0,099 s | 0,159 s |
| Plaque 3D percée | 1 696 | 5 802 tétraèdres | 0,304 s | 0,191 s |
| Coque sphérique | 382 | 760 triangles | 0,171 s | 0,158 s |
| Boîte 20×16×8 mm, maille cible 0,65 mm | 9 156 | 44 895 tétraèdres | 0,668 s | Non mesuré |

Les éléments de bord sont en plus des éléments du domaine indiqués. Les rapports des quatre cas sont admissibles selon leur politique : aucun élément invalide détecté. Les exemples utilisent leurs réglages livrés ; la boîte emploie Delaunay, ordre 1, sans zone critique, avec un minimum de qualité exigé de 0,05. Ce n'est pas le même cas que l'ancienne boîte Linux avec douze zones exigeant des seuils élevés : on ne peut pas attribuer leur différence de temps à la seule machine ou à la nouvelle optimisation.

Pour la boîte dense, le plus haut RSS des processus Rust lancés par le processus de mesure est **123 322 368 octets**, soit **117,61 Mio**. Le pic propre au processus Python est **75 005 952 octets**, soit **71,53 Mio**. Ces pics sont séparés et ne décrivent pas une mesure simultanée de la mémoire de toute l'application Qt. Les autres cas n'ont pas de mesure RSS dédiée. Une CAO complexe peut exiger beaucoup plus de mémoire, indépendamment du nombre final d'éléments.

Les petits exemples sont donc utilisables immédiatement sur ce Mac. Les temps ne garantissent aucune vitesse sur des volumes industriels, des défauts complexes, des couches très fines ou des maillages de millions d'éléments. Les recherches à plusieurs recettes et les exports multiples ajoutent leurs coûts.

Sources : `verification/local_m3/meshing.json` et `dense.json`. Reproduction des trois exemples :

```sh
.venv/bin/python scripts/benchmark_local.py --repetitions 3
```

## Un cycle réel de corpus et d'entraînement

Un nouveau pilote a été produit sur le Mac, sans réutiliser les lignes des preuves Linux : 24 cas structurés et 24 aléatoires, deux stratégies par cas, 24 familles. Deux lots de 24 ont été exécutés dans deux objets `NativeSession` successifs, pour contrôler la reprise.

| Phase ou résultat | Observation |
| --- | --- |
| Planification | 0,021 s ; aucun maillage à cette étape |
| Premier lot de 24 cas | 3,737 s |
| Deuxième lot repris, 24 cas | 4,735 s |
| Total production des 48 cas | 8,472 s |
| Inspections et évaluations backend | 140 appels, dont les inspections |
| Cas ayant produit un maillage | 46 sur 48 |
| Cas ayant une stratégie admissible | 45 sur 48 |
| Stratégies | 96 résultats : 88 admissibles et 8 refusés |
| JSONL produit | 666 857 octets, sans conservation de tous les maillages |
| Entraînement corpus, 3 époques, mini-lots de 16 | 0,810 s, pont et publication compris |
| Réseau | 128→32→1, 4 161 paramètres |
| Répartition des cas | 32 apprentissage, 8 validation, 8 test |
| Poids `current.json` | 125 518 octets |
| Checkpoint de reprise | 370 945 octets |
| Décision d'activation | `deploy_enabled=false` |

Deux cas sont des géométries invalides attendues. Ils sont enregistrés comme refus, sans maillage accepté artificiellement. Le réseau a été entraîné et sauvegardé ; la récompense de sélection en validation vaut **0,75212659**, identique à celle de la référence. En test, elle vaut **0,84445310**, également identique à la référence. L'activation reste donc refusée. Ce pilote confirme le fonctionnement de la chaîne sur le Mac ; il ne démontre pas que l'IA améliore déjà les maillages.

Le ZIP `output/partage/Modele_corpus_M3_pilote.zip` contient ces poids, un manifeste et les instructions, pour **42 015 octets**. Il conserve la décision de non-activation. Aucun million de configurations n'a été généré ni entraîné pendant cette intervention. Les 0,810 seconde ne sont pas une estimation d'entraînement d'un million de cas.

Une projection purement linéaire des 8,472 secondes sur un million de cas donnerait environ **49 heures à deux stratégies**, sur des configurations comparables à ce minuscule pilote. Cette arithmétique ignore les effets d'échelle, les géométries plus difficiles, les contrôles et les changements de distribution ; elle ne constitue pas une prévision de campagne. Mesurer d'abord un lot plus représentatif, puis plusieurs segments, permet de construire un budget crédible. Quatre stratégies changent le coût et le volume des journaux.

Source : `verification/local_m3/pilot.json`. Les commandes de reproduction du pilote figurent dans le guide complet.

## Optimisations nouvelles vérifiées

### Réseau interactif

La version initiale bornait chaque caractéristique à chaque neurone, puis à nouveau pour le gradient. La révision prépare une fois le vecteur borné et le réutilise. Le test Rust compare l'ancien calcul et le nouveau sur plusieurs graines et valeurs limites ; les poids et prédictions sont identiques bit à bit.

Le benchmark isolé utilise 8 192 exemples synthétiques et 16 passes, cinq répétitions, avec la même chaîne de compilation pour les deux sources. Il a été relancé après la suite de tests.

| Mesure MLP seul, entrées dans [0,1] | rc5 initiale | Révision | Rapport de vitesse |
| --- | ---: | ---: | ---: |
| Inférence | 0,121729 s | 0,108129 s | 1,13× |
| Gradients / SGD | 0,201809 s | 0,161080 s | 1,25× |

Sur la fixture avec entrées saturées, les rapports valent environ 1,12× et 1,26×. La génération CAO, les maillages, la préparation de l'historique et la publication des modèles sont exclus de ce benchmark. Le gain du noyau SGD n'est donc pas un gain global de 25 % sur l'application ou les maillages.

Source retenue : `verification/local_m3/live_inputs_confirmed.json`. La première série `live_inputs.json` est conservée comme mesure exploratoire ; ses médianes ne sont pas utilisées pour les chiffres ci-dessus.

### Téléchargements de l'API

Les fichiers sont envoyés par blocs de 1 Mio ; le serveur évite de charger l'aperçu sur les routes de téléchargement et de rapport. Un export contenant plusieurs fichiers est assemblé dans un ZIP temporaire sur disque. Le résultat téléchargé reste identique et les tests vérifient que les gros fichiers ne sont pas lus entièrement par `read_bytes`.

Mesure isolée sous Python 3.12, fichier creux de 128 Mio envoyé vers `/dev/null`, dans deux processus avec les mêmes imports : pic RSS de **161 857 536 octets (154,36 Mio)** pour l'ancienne lecture complète, contre **29 655 040 octets (28,28 Mio)** pour le transfert par blocs. Environ **126,08 Mio de pic sont évités** sur ce scénario. Ce n'est pas une mesure de débit réseau, ni de génération. L'upload base64 reste intégralement décodé en mémoire.

Source : `verification/local_m3/download_memory.json`. Reproduction :

```sh
.venv/bin/python scripts/benchmark_download_memory.py
```

### Installation allégée et portable

L'application importe QtCore, QtGui et QtWidgets ; le paquet **PySide6-Essentials** suffit à ces usages. Le projet demandait auparavant le méta-paquet PySide6, dont la résolution 6.12 sur ce Mac téléchargeait aussi WebEngine, PDF et Addons, inutilisés par l'application. Les tailles annoncées par le résolveur pour ces trois roues étaient respectivement environ 238,2, 8,2 et 86,4 Mio, soit **environ 333 Mio de téléchargements évités** pour cette résolution précise. Ce chiffre est une taille de téléchargement, pas une mesure de l'économie décompressée sur tous les systèmes.

Les 163 tests Python, dont les tests Qt exécutés en mode offscreen, passent avec Essentials seul. Les composants inutiles ne sont pas installés. Les téléchargements et caches temporaires de préparation ont été nettoyés dans le seul dossier du projet. L'installateur contrôle désormais l'exécution du noyau avec `--version`, suit la priorité du pont natif et compile Cargo avec `--locked`. Le lanceur contrôle aussi les bibliothèques nécessaires et borne ses vérifications par un délai.

La documentation officielle des paquets Qt confirme la séparation des modules : [Qt for Python, Package Details](https://doc.qt.io/qtforpython-6/package_details.html), [PySide6-Essentials](https://pypi.org/project/PySide6-Essentials/).

## Place occupée après préparation

Mesure des longueurs logiques de fichiers, sans suivre les liens symboliques, après nettoyage du cache de téléchargement. Les tailles peuvent évoluer après de nouveaux calculs ou une compilation.

| Dossier | Taille observée | Nécessité et diffusion |
| --- | ---: | --- |
| `.venv/` | 509,70 Mo | Dépendances d'exécution ; recréer chez le destinataire |
| `.runtime/` | 70,67 Mo | Python 3.12 local ; nécessaire à cet environnement précis |
| `.bootstrap/` | 36,93 Mo | Outil de préparation uv ; inutile au calcul courant |
| `.toolchain/` | 493,71 Mo | Rust et cache Cargo ; utile pour développer/recompiler |
| `rust_core/target/` | 64,45 Mo | Compilation et tests ; seul le binaire release est nécessaire au calcul |
| `output/` | 41,50 Mo lors du relevé | Maillages de mesure et pilote ; augmente à l'usage |
| Sources, exemples, preuves et documentation | Environ 75 Mo avant les documents bureautiques finaux | Livrable partageable, détails dans l'inventaire |

L'ensemble local de développement est d'environ **1,29 Go** à ce relevé, contre environ **0,66 Go** pour les sources/preuves, l'environnement Python, le Python local et le noyau nécessaires à l'exécution, hors nouveaux résultats. Le destinataire reçoit une archive source bien plus petite : il n'a pas à recevoir les 1,29 Go du poste de développement. Les environnements `.venv` ne sont pas des installations portables à recopier entre machines.

Les fichiers de poids représentent quelques dizaines ou centaines de ko. L'entraînement d'un corpus plus grand ne les fait pas croître proportionnellement si l'architecture reste identique. Le stockage du corpus, les sauvegardes et les maillages conservés dominent la croissance. L'inventaire exact actualisé est dans `verification/local_m3/inventory.json` ; le manifeste d'archive décrit le contenu exact du ZIP final.

## Validations et utilisation immédiate

**163 tests Python réussis en 29,300 s, aucun ignoré ; 111 tests Rust réussis en 4,79 s, aucun ignoré ni échec.** Les temps Rust excluent la compilation de la suite. Le binaire release a été compilé pour Apple M3 avec les dépendances verrouillées. Les avertissements de code Rust inutilisé ne sont pas des échecs de tests.

Le client de la documentation HTTP a aussi été exécuté sur un serveur local temporaire pour une plaque et pour le STEP fourni à deux volumes : les calculs et téléchargements ZIP aboutissent. Les rapports et conversions sont contrôlés par les tests ; aucun nouveau calcul de solveur Cast3M/Abaqus/ANSYS n'a été réalisé ici. Windows, Linux avec cette révision locale et les autres configurations macOS nécessitent leurs propres vérifications.

Sur ce Mac, depuis le dossier `atelier_mailleur/` :

```sh
sh Lancer.command
.venv/bin/python -m atelier doctor
```

Le lanceur peut être exécuté avec le Python système car l'environnement `.venv` compatible existe déjà. Pour recompiler avec la chaîne isolée :

```sh
export CARGO_HOME="$PWD/.toolchain/cargo"
export RUSTUP_HOME="$PWD/.toolchain/rustup"
export PATH="$CARGO_HOME/bin:$PATH"
cargo build --locked --release --manifest-path rust_core/Cargo.toml
```

Le ZIP final inclut le noyau Apple M3 de cette révision. Par défaut, l'empaquetage exclut les binaires des autres plateformes qui n'ont pas été recompilés ici, afin qu'un destinataire reconstruise les sources modifiées avec Cargo. Il n'inclut ni `.venv`, ni Python local, ni Rust, ni les caches. Le paquet n'est pas une application macOS signée/notariée.
