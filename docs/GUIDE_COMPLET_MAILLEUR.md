# Comprendre, utiliser et partager Mailleur

## 1. Version étudiée et réponse pratique

Ce guide décrit **Atelier Mailleur 1.0.0rc5, révision locale du 4 novembre 2026**. Les sources ont été récupérées depuis le chat « Optimiser le mailleur », dans le projet « Créations de mailleur ». L'archive initiale a été vérifiée contre son manifeste SHA-256 : 1 326 fichiers vérifiés, ZIP de 20 319 571 octets. Les résultats Linux déjà livrés sont conservés ; les nouvelles mesures sur Apple M3 sont rangées dans `verification/local_m3/`.

Le dossier actif est `atelier_mailleur/`. Le dossier « mailleur automatique » présent auparavant sur cet ordinateur ne contenait aucun code. L'ancienne synthèse « Mailleur v2.5 » décrit une architecture différente et précise qu'elle a été reconstituée après effacement de son espace de travail. Le README de la rc5 indique que l'archive Rust d'origine n'a pas été retrouvée. Les annonces de GNN, PPO, tout-hexa et protocole JSON-lines de cette ancienne synthèse ne décrivent pas les sources récupérées ici.

**Le MacBook Pro Apple M3 à 16 Go convient au démarrage, aux maillages de démonstration et à l'entraînement des petits réseaux de cette version.** Son GPU à 10 cœurs n'est pas utilisé par l'entraîneur actuel. La production des maillages d'apprentissage, bien plus que la taille des poids, détermine le coût d'une grande campagne. Une compatibilité matérielle ne garantit pas qu'une pièce industrielle donnée tiendra dans 16 Go.

La machine disposait de Python 3.9.6, incompatible avec les exigences du projet, et d'aucune chaîne Cargo. Python 3.12 et Rust ont été installés dans des dossiers isolés du projet pour les vérifications. Le système et le profil du terminal ne sont pas modifiés. Le détail mesuré de l'environnement et les résultats figurent dans `verification/local_m3/` et dans le document `MESURES_M3.md`.

## 2. Ce que fait le logiciel

Mailleur prépare une géométrie, choisit des réglages de maillage, génère des éléments, contrôle leur qualité et écrit des fichiers destinés à un solveur. Il peut créer des primitives ou importer une pièce CAO. Le mode plan traite un domaine 2D, le mode surface une peau, et le mode volume un solide.

Les surfaces sont maillées en triangles, avec possibilité de recombinaison en quadrilatères. Les volumes sont maillés en tétraèdres. La version ne fournit pas un mailleur automatique tout-hexa pour des volumes arbitraires. Les éléments d'ordre 2 sont possibles selon le type et le format de sortie réellement pris en charge.

La « recherche » essaie plusieurs recettes de maillage : algorithme, tailles et réglages locaux. Elle retient la meilleure recette admissible parmi celles essayées dans le budget. Elle ne prouve pas que le maillage est optimal parmi toutes les solutions possibles.

Le logiciel peut mémoriser une recette réussie et la réexécuter sur une pièce identifiée. Le réseau prédit l'intérêt de recettes et peut contribuer à leur classement lorsqu'un gain de validation est démontré. Il ne dessine pas directement chaque nœud et ne remplace pas le moteur géométrique.

Les zones « contact », « interface » ou « contrainte » imposent des exigences locales de qualité. Elles doivent être désignées à partir du problème physique. Le programme ne calcule ni le champ de contraintes, ni les matériaux, ni les charges, ni une estimation d'erreur éléments finis. Un bon indicateur de forme ne dispense donc pas d'une étude de convergence dans le solveur.

## 3. Architecture et circulation des données

```text
Utilisateur ou programme de calcul
              |
      Interface Qt / CLI / API HTTP (Python)
              |
      NativeSession : requête JSON sur disque
              |
      mailleur-core --request native_request.json (Rust)
              |
      API C de Gmsh -> OpenCASCADE / algorithmes natifs C++
              |
      géométrie, maillage, qualité, recherche, apprentissage
              |
      report.json + preview.json + mesh.msh + model.brep
              |
      Python : affichage, fichiers, conversion/export, réponse API
```

| Couche | Fichiers principaux | Responsabilité |
| --- | --- | --- |
| Interface de bureau | `atelier/desktop.py`, `viewport.py`, `desktop.qss` | Fenêtres, formulaires, caméra, sélection, rendu et affichage des rapports |
| Commandes | `atelier/__main__.py` | Installation diagnostiquée, génération, apprentissage, corpus et serveur |
| API HTTP locale | `atelier/server.py`, `worker.py`, `web/` | File de travaux, processus isolés, suivi, annulation et téléchargements |
| Pont vers Rust | `atelier/native.py` | Normalisation, copie de sources, requêtes, délai, lecture des résultats |
| Moteur | `rust_core/src/backend.rs`, `gmsh.rs` | Géométrie, unités, maillage, remaillage et appels natifs |
| Contrôles | `quality.rs`, `metrics.rs` | Qualité complète, zones, compteurs et métriques |
| Apprentissage | `advisor.rs`, `learning.rs`, `experience*.rs`, `dataset_model.rs` | Prédiction, entraînement, sélection et mémoire |
| Corpus | `corpus.rs`, `corpus_generator.rs`, `representation.rs` | Configurations reproductibles, segments, reprise et 128 caractéristiques |
| Exports | `atelier/formats.py`, `convert_worker.py`, `mailleur/io.py` | Écriture et conversion via meshio/h5py ou adaptateurs ; unités numériques dans Rust |

Python effectue aussi les calculs nécessaires à la caméra et à l'affichage. Les convertisseurs réalisent les opérations de représentation et d'entrée/sortie. La séparation des responsabilités ne signifie donc pas « aucune opération arithmétique dans Python » : le calcul de géométrie, de maillage, de qualité et des réseaux appartient au noyau Rust et aux bibliothèques natives qu'il pilote.

Gmsh est chargé dynamiquement par `libloading`, via son API C. Le binaire Rust et la bibliothèque doivent correspondre au système et à l'architecture. Le petit binaire Linux x86_64 livré dans l'archive n'est pas exécutable sur Apple M3 ; le noyau doit être compilé pour `arm64`. `ATELIER_CORE` permet de choisir un noyau et `GMSH_LIBRARY` une bibliothèque explicite.

### Parcours d'une génération

1. Le projet JSON indique géométrie, mode, unités, tailles, critères et zones. Une CAO importée reste un fichier source séparé ou embarqué dans un projet portable `.amp`.
2. Python vérifie la structure, copie la source dans la session et écrit `native_request.json`.
3. Rust valide les valeurs numériques, convertit les coordonnées en mètres internes et prépare la CAO avec Gmsh/OpenCASCADE. Une réparation éventuelle utilise une tolérance explicite.
4. Le backend applique les groupes, les champs de taille et les correspondances demandées, puis lance le maillage.
5. Rust contrôle l'ensemble des éléments, notamment les Jacobiennes et la qualité de forme, ainsi que les exigences des zones. L'aperçu peut être échantillonné ; les compteurs ne se limitent pas à cet aperçu.
6. Le moteur écrit rapport et artefacts. Python les affiche ou les renvoie. Un maillage refusé ne devient pas exportable simplement parce qu'un fichier intermédiaire existe.
7. L'export convertit les mètres internes vers l'unité demandée, écrit le format cible et conserve un rapport. L'export Cast3M s'appuie sur un flux UNV et un script d'import ; il ne définit pas à lui seul un calcul mécanique complet.

La marge autour d'une zone repose sur une boîte englobante élargie : c'est une sélection conservatrice, qui peut couvrir davantage qu'une distance exacte à la CAO. Le remaillage local travaille sur les entités prises en charge et vérifie la conservation hors sélection ; il ne fournit pas une reconstruction arbitraire d'un sous-volume.

## 4. Trois réseaux et plusieurs sens du mot modèle

Un **modèle CAO** est une pièce. Une **configuration** ajoute des tailles, des unités et des objectifs à cette pièce. Une **stratégie** est un essai de réglages pour une configuration. Un **modèle entraîné** est un petit fichier de poids et de métadonnées. Le corpus contient les exemples ; les poids en sont une synthèse numérique, sans inclure tous les maillages.

| Réseau | Architecture | Paramètres | Usage |
| --- | --- | ---: | --- |
| Conseiller historique | 8 entrées, 12 neurones cachés, 3 sorties | 147 | Choisir entre trois stratégies du premier parcours de démonstration |
| Guide de mémoire interactive | 128 entrées, 12 neurones cachés, 1 sortie | 1 561 | Estimer l'intérêt d'une recette dans la recherche sur une pièce |
| Guide du corpus | 128 entrées, 32 neurones cachés par défaut, 1 sortie | 4 161 | Classer les recettes à partir du corpus segmenté |

Le noyau utilise du calcul CPU en double précision `f64`. Pour le guide 128→32→1, le compte est `128×32 + 32 + 32 + 1 = 4161`. Les poids bruts occupent 33 288 octets ; le JSON contient en plus les nombres sous forme de texte, les métriques et la provenance. La largeur du réseau corpus est configurable dans l'entraîneur natif ; le tableau décrit le réglage livré.

Les 128 caractéristiques résument des dimensions, modes, topologies et informations spatiales ainsi que la recette proposée. Il s'agit d'une représentation compacte, pas d'une description CAO complète ni d'un graphe GNN. Deux formes différentes peuvent rester difficiles à distinguer avec ces descripteurs.

Les réseaux de démonstration sont sauvegardés mais leur classement reste désactivé lorsque la validation ne montre pas de progrès. Dans l'état final des démonstrations initiales livrées, les trois guides sont désactivés (`deploy_enabled=false`). Le pilote de corpus ne démontre pas de gain de sélection. La recherche classique et le réemploi exact des recettes continuent de fonctionner. Activer manuellement un modèle pour afficher une « IA active » ferait perdre cette protection méthodologique.

### Recherche interactive et mémoire

`learn` examine une pièce, essaie un nombre borné de recettes, contrôle chacune, conserve la meilleure admissible et enregistre les réussites et les échecs. Le contexte inclut le contenu de la source et les paramètres, dont les critères de qualité. Le nom du fichier ne suffit pas à identifier une pièce.

`experience/memory.json` contient l'index ; `events.jsonl` conserve les événements ; `model.json` et ses versions conservent les poids. Une recette retrouvée est recalculée et contrôlée. L'index est limité à 1 000 contextes, 32 000 observations et 128 Mio ; le journal est limité à 512 Mio. Les limites provoquent un diagnostic, sans purge automatique. Cette mémoire n'est pas le stockage d'un million de configurations.

## 5. Corpus, entraînement et coût d'un million de cas

Le plan par défaut prévoit 250 000 configurations structurées et 750 000 aléatoires, réparties sur **24 familles**. Il n'exécute aucun de ces calculs lorsqu'on crée le plan. Les paramètres structurés suivent une répartition contrôlée ; les variantes aléatoires sont reproductibles à partir de la graine, de la famille et de l'indice, avec stratification marginale des paramètres.

Les familles couvrent plaques percées ou multi-trous, congés, chanfreins, équerres et polygones concaves, parois minces, sphères, boîtes, cadres, volumes perforés, bords minuscules et défauts invalides. La liste exacte et les règles sont dans `rust_core/src/corpus_generator.rs`. Il existe volontairement des cas invalides : enregistrer leur refus est utile, mais ils ne deviennent pas des maillages réussis.

Les familles sont réparties entre apprentissage (16), validation (4) et test (4). Les variantes d'une même famille restent dans la même partition. Les gradients utilisent uniquement l'apprentissage ; la validation sélectionne les poids et leur activation ; le test reste une évaluation distincte. Un million de variantes de ces 24 familles ne prouve pas la couverture de toutes les pièces industrielles.

Le pilote initial livré a réellement traité 48 configurations et 96 stratégies : 46 cas ont produit un maillage et 45 disposent d'au moins une stratégie admissible. Ces compteurs ne sont ni un million de maillages ni une preuve de généralisation. Les 33 010 cas synthétiques des benchmarks d'entraînement servent à tester les calculs et les fichiers JSONL ; leurs maillages n'ont pas été générés.

Les résultats vont dans `shards/cases-*.jsonl`. Chaque ligne contient une configuration et les observations de ses stratégies. Les maillages détaillés ne sont pas tous conservés par défaut. La reprise vérifie les empreintes et le curseur ; une altération de données durables est signalée. `--retain-meshes` augmente fortement le stockage.

L'entraînement lit progressivement les segments et forme des mini-lots. Il ne charge pas un million de maillages en RAM. Les poids et points de reprise sont distincts : les premiers servent à prédire ; les seconds incluent l'état nécessaire pour reprendre un apprentissage compatible. Les budgets de temps sont vérifiés entre étapes ; les contrôles d'intégrité et un appel Gmsh en cours ne deviennent pas instantanés à l'échéance.

### Prévoir les durées sans inventer une vitesse

Le coût est approximativement `N × (inspection + somme des stratégies + fichiers)`, auquel s'ajoutent les reprises et l'entraînement. Avec quatre stratégies, un million de configurations représente jusqu'à quatre millions d'essais, plus les inspections. Le temps par essai dépend fortement de la géométrie et de la finesse.

| Hypothèse illustrative par essai | Quatre millions d'essais séquentiels |
| --- | --- |
| 0,1 seconde | 4,63 jours |
| 1 seconde | 46,30 jours |
| 5 secondes | 231,48 jours |

Ces valeurs sont des scénarios arithmétiques, hors inspections et écritures, pas des prévisions du Mac. Augmenter le nombre de processus ne divise pas automatiquement le temps par le nombre de cœurs : RAM, température, accès disque et bibliothèques natives interviennent. Sur 16 Go, commencer avec un seul travail simultané et un lot de 20 à 48 cas permet de mesurer le besoin réel.

## 6. Tailles, mémoire et partage

L'archive initiale occupe **20,32 Mo compressés** et **74,32 Mo décompressés**. Environ 70,30 Mo proviennent des preuves et historiques `verification/`, soit 94,6 % de la livraison. Le code spécifique est petit : environ 238 ko de Python applicatif, 76 ko de scripts et 508 ko de sources Rust, avant cette révision. Un environnement d'exécution inclut aussi Python, Qt, Gmsh, NumPy et les convertisseurs ; le dossier de développement inclut en plus Rust et son cache de compilation.

| Objet livré dans les preuves initiales | Taille du fichier observé | À transmettre pour utiliser les poids ? |
| --- | ---: | --- |
| Guide historique JSON | 13 869 octets | Seulement pour le parcours historique |
| Guide interactif récent JSON | 49 964 octets | Pour transporter le réseau ; insuffisant seul pour reprendre l'apprentissage |
| Guide corpus récent JSON | 125 562 octets | Oui, pour ce guide |
| Point de reprise corpus | 371 034 octets | Pour reprendre l'entraînement, pas nécessaire à la prédiction |
| Expérience interactive récente complète | 1 428 396 octets | Pour reprendre l'historique interactif et réemployer ses recettes |
| Pilote JSONL, 48 cas et 2 stratégies | 666 890 octets | Pour audit ou nouvel entraînement |

Une extrapolation proportionnelle du pilote donne **13,89 Go de JSONL pour un million de cas à deux stratégies**, hors maillages et sauvegardes. Quatre stratégies augmentent cette quantité ; elles ne doublent pas exactement le total puisque certaines métadonnées sont communes. Ce chiffre repose sur un petit échantillon et doit être recalibré sur un lot plus représentatif. À 1 Mo par maillage conservé, quatre millions d'essais ajouteraient 4 To ; à 10 Mo, 40 To. Ces exemples expliquent pourquoi il faut garder les journaux utiles et conserver les maillages de manière sélective.

Les poids ne grossissent pas proportionnellement au nombre de configurations tant que l'architecture reste fixe. Un modèle entraîné sur beaucoup plus de cas peut rester de l'ordre de centaines de ko. Ce sont les expériences, les journaux, les versions, les copies de sources et les exports qui augmentent.

Consulter `TAILLES_ET_CAPACITES.md` pour l'inventaire détaillé et `MESURES_M3.md` pour la taille réellement installée. `scripts/inventory_sizes.py` reproduit les mesures en séparant sources, preuves, dépendances et outils. La taille des fichiers n'est pas la RAM consommée pendant un maillage.

### Envoyer le modèle à une autre personne

Le destinataire a besoin du code compatible et de ses dépendances, puis du fichier de poids correspondant au bon rôle. Un JSON de poids ne constitue pas une application autonome. Le binaire Rust est propre à la plateforme ; les fichiers JSON de poids sont portables entre plateformes compatibles.

Le guide corpus se place dans `DOSSIER_ATELIER/models/corpus.json` pour le parcours `learn`, dont l'option `-o DOSSIER_ATELIER` définit l'espace de travail. Le guide historique s'utilise en CLI avec `mesh --advisor chemin/guide.json` ; le pont Python conserve sa copie dans `DOSSIER_ATELIER/models/guide.json`. Sa présence seule ne demande pas son utilisation à une commande `mesh` sans `--advisor`. Ces fichiers ont des schémas différents : ne pas les interchanger, ni modifier artificiellement leur indicateur d'activation.

Le guide interactif appartient au dossier mémoire, sous `DOSSIER_MEMOIRE/model.json`, sélectionné avec `learn --memory DOSSIER_MEMOIRE`. **Copier uniquement ce JSON dans une mémoire neuve ne permet pas de poursuivre son apprentissage.** La mise à jour cumulative réclame les observations des groupes déjà appris et signale un historique incomplet si elles manquent. Pour poursuivre ce guide ou réemployer ses recettes, transférer un dossier `experience/` cohérent, comprenant index, journal, sources et modèles, avec les calculs arrêtés et après examen de son contenu. Le modèle seul reste transportable pour examiner ses poids et ses métadonnées ; il ne constitue pas un import autonome de mémoire.

`scripts/share_model.py` produit un ZIP léger avec modèle, manifeste d'intégrité et instructions, sans copier automatiquement de corpus, dossier mémoire ou fichier CAO séparé. Pour le guide historique, il retire aussi le champ `training_configs`, qui peut embarquer des géométries et n'est pas requis par le chargeur de prédiction ; les poids, les preuves et l'état d'activation sont conservés. Les paquets de démonstration de `output/partage/` occupent environ 42 ko pour le corpus, 19 ko pour l'interactif et 4 ko pour l'historique. Ils restent désactivés comme les modèles sources. Conserver un jeu d'exemples publics de validation facilite la vérification par le destinataire.

## 7. Installer et commencer

Pour une installation neuve, utiliser Python 3.11, 3.12 ou 3.13 et Cargo si aucun noyau de la plateforme n'est fourni. Les commandes suivantes sont à lancer depuis `atelier_mailleur/`. Le premier téléchargement nécessite Internet ; les calculs fonctionnent ensuite localement.

```sh
python3.12 scripts/install.py
python3.12 scripts/launch.py
```

Sur ce Mac déjà préparé, le lanceur utilise l'environnement local :

```sh
sh Lancer.command
.venv/bin/python -m atelier doctor
.venv/bin/python -m atelier mesh examples/atelier_plate.json \
  -o output/premier_maillage --formats msh,inp
```

Pour apprendre sur une pièce en gardant une mémoire commune :

```sh
.venv/bin/python -m atelier learn examples/apprentissage_plaque_zones.json \
  -o output/mon_atelier --max-trials 4 --epochs 20 \
  --max-seconds 60 --formats msh
.venv/bin/python -m atelier memory --workspace output/mon_atelier
```

Pour une CAO importée, utiliser un projet dont `geometry.kind` vaut `import` et fournir `--source piece.step`. Un projet portable `.amp` contient déjà sa source. Toujours contrôler les unités d'entrée, le mode et la qualité avant utilisation dans un solveur.

Pour constituer un pilote équilibré puis l'entraîner :

```sh
.venv/bin/python -m atelier corpus plan --directory output/pilote \
  --workspace output/mon_atelier --structured 24 --random 24 --strategies 2
.venv/bin/python -m atelier corpus run --directory output/pilote \
  --workspace output/mon_atelier --limit 48 --max-seconds 120
.venv/bin/python -m atelier corpus train --directory output/pilote \
  --workspace output/mon_atelier --epochs 3 --batch-size 16 \
  --max-records 0 --max-seconds 60
.venv/bin/python -m atelier corpus status --directory output/pilote \
  --workspace output/mon_atelier
```

Pour la campagne cible, créer un autre dossier avec les quotas 250000 et 750000. Relancer `corpus run` traite le lot suivant. Le logiciel ne lance pas automatiquement toute la campagne. Le corpus se planifie aussi depuis Qt. Le guide est copié dans le dossier de l'atelier après entraînement ; sa présence ne prouve pas son activation.

## 8. Fonctionnement de l'API

Deux interfaces de programmation coexistent : la bibliothèque Python `NativeSession` et l'API HTTP locale. Le pont interne vers Rust utilise un fichier JSON par requête et un nouveau processus `mailleur-core --request FICHIER`. Cette version n'utilise pas un serveur Rust permanent en JSON-lines.

```sh
.venv/bin/python -m atelier serve --host 127.0.0.1 --port 8765 \
  --workspace output/api --workers 1 --timeout 300 --no-browser
```

Le client lit `GET /api/info` pour connaître le moteur, les formats et le jeton de session. Il envoie ensuite `POST /api/jobs` avec le projet JSON et l'en-tête `X-Atelier-Token`. Le serveur répond immédiatement avec un identifiant et l'état `queued` ; un processus de travail lance Rust et les exports. Le client suit `GET /api/jobs/{id}` jusqu'à `completed`, `failed` ou `cancelled`, puis récupère `/bundle` ou `/download?format=msh`. Le rapport est disponible à `/report`.

`POST /api/uploads` reçoit une CAO encodée en base64 et renvoie `upload_id`. Le client joint cet identifiant au projet importé. La limite de source est 50 Mio ; base64 augmente le volume transféré d'environ un tiers. Cette entrée est conservée en mémoire pendant le décodage ; l'optimisation par blocs porte sur les sorties téléchargées.

`POST /api/learning` recherche et mémorise une recette sur la pièce ; `/api/training` lance le conseiller historique. Ces routes ne sont pas l'entraîneur du grand corpus. Le corpus reste accessible en CLI, Python et interface Qt. La file accepte 16 travaux en attente ou actifs ; le nombre de travailleurs est réglable de 1 à 8. Le délai du serveur couvre le traitement et les exports.

Ce serveur est prévu pour l'ordinateur local. Son jeton protège les mutations et les contrôles d'hôte/origine protègent le contexte navigateur ; le jeton est accessible via `/api/info` aux clients qui peuvent joindre le service. Il ne constitue pas une authentification multi-utilisateur pour un service public. Pour des personnes distantes, le partage du projet et du modèle permet à chacun de lancer son instance locale. Un hébergement partagé demanderait une conception dédiée de l'authentification, des espaces utilisateurs, des quotas et du déploiement.

Le document `API_UTILISATION.md` donne toutes les routes, un client Python complet et les commandes cURL. Le fonctionnement par fichiers facilite le diagnostic : un dossier de travail conserve requête, rapport, journal, maillage et exports. Un échec du processus retourne un diagnostic ; au redémarrage les travaux interrompus sont marqués en échec, tandis que la reprise du corpus suit ses points de reprise propres.

## 9. Optimisations appliquées et améliorations suivantes

La rc5 initiale contenait déjà des optimisations d'entraînement, de reprise, de qualité et de rendu. Leurs mesures Linux sont dans `OPTIMISATIONS_RC5.md`. Elles ne doivent pas être présentées comme des résultats mesurés sur Apple M3.

Cette révision réduit les dépendances Qt aux modules utilisés, transfère les gros téléchargements HTTP par blocs, évite le chargement des aperçus sur les routes de fichiers et de rapports, et vérifie l'exécution du noyau lors de l'installation. Les dépendances Cargo restent verrouillées. L'apprentissage interactif borne chaque vecteur d'entrée une fois et le réutilise, avec comparaison numérique bit à bit contre le calcul initial. Les gains retenus et leurs limites sont détaillés dans `MESURES_M3.md`.

Pour améliorer la qualité de l'apprentissage, la priorité est de mesurer le gain de sélection sur des familles et des pièces réservées, puis d'augmenter la diversité utile. Un seuil d'activation basé sur la validation est plus informatif que le seul nombre d'époques. Les géométries réelles nouvelles et les cas difficiles doivent être examinés séparément des variantes très proches.

Pour accélérer davantage, profiler d'abord la génération, l'inspection, les contrôles, les écritures et les exports sur une charge représentative. Si la CAO domine, accélérer les quelques milliers de poids aura peu d'effet sur le temps total. Un cache persistant doit intégrer source, unités, critères et versions numériques ; une ancienne réussite doit être revalidée. Le parallélisme exige des dossiers et mémoires cohérents par travail, des budgets et des mesures de RAM. Ajouter un GPU aux petits réseaux ne garantit pas un progrès.

Le tout-hexa, une représentation GNN, une adaptation pilotée par le solveur ou un service HTTP multi-utilisateur sont des évolutions architecturales. Elles demandent des objectifs et des validations distincts ; elles ne sont pas des options déjà disponibles dans cette livraison.

## 10. Préparer GitHub et contribuer

Publier les sources, README, documentation, exemples publics, tests, verrou Cargo et workflows CI. Le `.gitignore` fourni exclut les environnements, la chaîne de compilation et les dossiers de travail usuels. Il ne peut pas deviner toutes les CAO privées : examiner les fichiers retenus avant tout envoi. Le projet n'a pas été publié sur GitHub par cette intervention.

Distribuer les poids dans un ZIP de modèle ou une release, avec architecture, schéma, version, provenance des données, métriques et état d'activation. Garder les gros corpus et leurs copies hors de l'historique Git courant ; un fichier de poids n'a pas besoin de transporter plusieurs gigaoctets de journaux. Les preuves de livraison servent de références techniques, sans être un corpus de production.

Le code propre au projet n'a pas encore de licence de publication choisie par son titulaire. Le fichier `THIRD_PARTY.md` répertorie les composants tiers et doit accompagner la diffusion. Gmsh est distribué sous GPL avec ses conditions propres ; Qt/PySide dispose également de conditions spécifiques. Choisir une licence pour le code personnel ne remplace pas les notices des dépendances. Aucune licence ni identité d'auteur n'a été inventée dans cette révision.

Un contributeur peut partir des commandes suivantes, après installation :

```sh
cargo test --locked --release --manifest-path rust_core/Cargo.toml
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/benchmark_local.py
```

Sur ce Mac, la chaîne Rust isolée demande les variables `CARGO_HOME` et `RUSTUP_HOME` pointant vers `.toolchain/cargo` et `.toolchain/rustup`. Pour une installation normale de Rust, ces variables spécifiques ne sont pas nécessaires. Le script d'empaquetage réutilise le binaire compilé localement et crée un manifeste SHA-256 ; l'archive produite n'embarque pas les environnements de développement.

Comparer une modification sur les mêmes entrées, graines, critères et versions de bibliothèques. Séparer temps de calcul, qualité, taux d'échec, RAM, écritures et conversions. Une optimisation qui change les nœuds exige une étude de qualité ; une optimisation supposée identique doit comparer ses résultats. La CI proposée pour trois systèmes reste distincte des tests réellement exécutés localement et des essais à mener dans Cast3M, Abaqus ou ANSYS.

## 11. Petit glossaire

| Terme | Sens dans ce projet |
| --- | --- |
| CAO | Géométrie source, avec surfaces, arêtes, volumes et topologie |
| Maillage | Nœuds et connectivité des éléments utilisés par un calcul |
| Jacobienne | Indicateur lié à l'orientation et à la transformation d'un élément |
| minSICN | Indicateur de forme normalisé utilisé dans le contrôle de qualité |
| Recette | Ensemble de paramètres de maillage à exécuter et contrôler |
| Corpus | Configurations et résultats d'essais servant à l'apprentissage |
| Époque | Passage d'entraînement sur les exemples effectivement disponibles |
| Mini-lot | Groupe borné d'exemples utilisé pour une mise à jour des poids |
| Checkpoint | État permettant de reprendre un entraînement compatible |
| Inférence | Calcul d'une prédiction à partir de poids déjà entraînés |
| API | Contrat permettant à un autre programme de demander des opérations |
| CLI | Interface par commandes dans un terminal |

## 12. Références et preuves

Le code de `atelier/` et `rust_core/src/` fait foi pour cette version. Les documents `ARCHITECTURE.md`, `CORPUS_ET_LANGAGES.md`, `APPRENTISSAGE_CONTINU.md`, `FORMATS.md` et `LIMITES.md` développent les contrats spécialisés. `TAILLES_ET_CAPACITES.md`, `API_UTILISATION.md` et `MESURES_M3.md` complètent ce guide.

Les preuves initiales sont dans `verification/native_release/`, `verification/corpus/`, `verification/continuous_learning/` et `verification/optimization/`. Les nouvelles preuves sont dans `verification/local_m3/`. Les deux provenances restent séparées.

Références officielles consultées pour les dépendances : [Gmsh et son API](https://gmsh.info/doc/texinfo/gmsh.html), [modules des paquets Qt for Python](https://doc.qt.io/qtforpython-6/package_details.html), [PySide6-Essentials](https://pypi.org/project/PySide6-Essentials/), [gestion de Python par uv](https://docs.astral.sh/uv/guides/install-python/). Les tailles installées proviennent de mesures locales, pas des tailles annoncées sur ces pages.
