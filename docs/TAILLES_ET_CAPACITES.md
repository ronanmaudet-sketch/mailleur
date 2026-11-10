# Tailles, corpus et partage des modèles

Relevé du 4 novembre 2026, fondé sur les fichiers de l'archive **Atelier_Mailleur_Rust_Python_rc5.zip** récupérée depuis la bibliothèque du projet. Les chiffres de cette première section décrivent cette archive d'origine, avant les modifications locales de documentation et de code. Les autres sections indiquent les tailles des exemples réellement présents et les hypothèses d'extrapolation.

**Le logiciel et ses modèles appris sont petits ; les historiques et les maillages conservés occupent l'essentiel de l'espace.** Un million de configurations ne signifie pas un modèle d'intelligence artificielle contenant un million de pièces.

## Unités et méthode

- 1 ko = 1 000 octets ; 1 Mo = 1 000 000 octets ; 1 Go = 1 000 000 000 octets.
- 1 Kio = 1 024 octets ; 1 Mio = 1 048 576 octets ; 1 Gio = 1 073 741 824 octets.
- Les tailles mesurées sont les longueurs des fichiers, hors blocs du système de fichiers. Elles ne sont ni la mémoire vive consommée ni les téléchargements nécessaires à l'installation.
- Le ZIP est compressé. Une fois extrait, ses fichiers occupent davantage d'octets. Les environnements Python, caches et fichiers de compilation créés ensuite constituent un poste supplémentaire.

Le script fourni permet de refaire le relevé après toute modification :

```sh
python3 scripts/inventory_sizes.py
python3 scripts/inventory_sizes.py --json
```

Il ne suit pas les liens symboliques et ne lit pas les contenus des corpus. Il sépare `.venv`, `.bootstrap`, `.runtime`, `.toolchain`, `target`, caches, sorties `output`/`outputs`, paquets et historique Git des fichiers du projet. Les données de validation de `verification/` restent comptées dans le projet et sont identifiées séparément. Un fichier JSON de relevé peut être conservé hors du dossier mesuré pour ne pas s'inclure lui-même dans le relevé suivant.

## Taille de l'archive rc5 récupérée

| Ensemble | Taille mesurée |
| --- | ---: |
| Archive ZIP, 1 327 fichiers | 20 319 571 octets = **20,32 Mo / 19,38 Mio** |
| Fichiers extraits, avant installation | 74 315 966 octets = **74,32 Mo / 70,87 Mio** |
| Données, rapports et sources de référence `verification/` | 70 298 468 octets = **70,30 Mo** |
| Ensemble hors `verification/` | 4 017 498 octets = **4,02 Mo** |

Environ **94,6 %** des octets décompressés proviennent de `verification/`. Ce dossier contient des preuves de validation, des essais, des maillages, des sources CAO, des copies de modèles et les sources rc4 servant aux comparaisons. Il n'est pas nécessaire de tout joindre à chaque envoi d'un modèle appris. Le conserver dans une livraison de référence facilite cependant la vérification des résultats annoncés.

| Poste de l'archive d'origine | Octets | Rôle |
| --- | ---: | --- |
| `atelier/*.py` : 12 fichiers | 221 139 | Interface, transport vers Rust, orchestration et échanges de fichiers |
| `mailleur/*.py` : 7 fichiers | 16 659 | Commandes et compatibilité |
| `scripts/*.py` : 8 fichiers | 75 614 | Installation, lancement, validation, mesures et emballage |
| `rust_core/src/*.rs` : 13 fichiers | 508 331 | Moteur, qualité, corpus, entraînement et prédiction |
| `rust_core/Cargo.toml` et `Cargo.lock` | 5 679 | Configuration et versions de dépendances Rust |
| Binaire livré `rust_core/bin/linux-x86_64/mailleur-core` | 2 471 456 | Exécutable **Linux x86_64**, incompatible directement avec le Mac M3 |
| Ressources d'interface de `atelier/`, hors Python | 106 755 | Feuille Qt, HTML, CSS et JavaScript |
| `tests/` | 200 100 | 19 fichiers Python exécutables et références textuelles retirées |
| `examples/` : 22 fichiers | 62 449 | Projets et géométrie d'exemple, dont un STEP de 32 520 octets |
| `docs/` | 85 374 | Huit documents Markdown techniques |
| `README.md` et `NOUVEAUTES.md` | 30 788 | Présentation et historique |
| `MANIFEST.sha256` | 229 986 | Empreintes de la livraison d'origine |

Ces lignes détaillent les postes principaux ; des petits lanceurs et configurations complètent le total. Les fichiers Markdown et scripts ajoutés par l'audit local augmentent légèrement ces chiffres. Le manifeste d'origine ne certifie pas les fichiers modifiés après extraction : il faut produire un nouveau manifeste lors d'une nouvelle livraison.

## Ce que signifie « modèle »

Le projet manipule trois objets différents :

| Objet | Exemple | À quoi il sert |
| --- | --- | --- |
| Géométrie ou projet | `.step`, `.brep`, `.amp`, configuration JSON | Décrit la pièce et les réglages à mailler |
| Maillage calculé | `.msh`, `.unv`, `.inp` | Contient les nœuds, éléments et groupes pour le calcul |
| Modèle appris | `model.json`, `corpus.json`, `guide.json` | Contient les poids d'un petit réseau et ses métadonnées de validation |

Le réseau conseille l'ordre ou le choix de recettes. **Gmsh/OpenCASCADE, piloté par Rust, construit la géométrie et le maillage.** Les poids seuls ne contiennent ni le moteur Gmsh, ni un maillage prêt à calculer, ni toutes les géométries d'entraînement.

## Taille des réseaux et des fichiers appris

Les poids sont stockés dans du JSON avec leurs dimensions, leur schéma de caractéristiques, leurs métriques et leur provenance. Ils ne sont pas livrés en PyTorch ou safetensors dans cette rc5.

| Réseau présent dans les sources rc5 | Architecture | Paramètres | Poids seuls en f64, calcul théorique | Fichier réel de démonstration |
| --- | --- | ---: | ---: | ---: |
| Guide historique de choix d'algorithme | 8 entrées → 12 neurones → 3 sorties | 147 | 1 176 octets | `guide.json` : **13 869 octets** |
| Guide de mémoire interactive, schéma v2 | 128 → 12 → 1 | 1 561 | 12 488 octets | `experience/model.json` : **49 964 octets** |
| Guide entraîné sur le corpus | 128 → 32 → 1 par défaut | 4 161 | 33 288 octets | `models/current.json` ou copie `models/corpus.json` : **125 562 octets** |
| Point de reprise du corpus | État permettant de reprendre l'entraînement | — | — | `training.checkpoint.json` : **371 034 octets** |

Les tailles réelles ci-dessus sont mesurées dans les preuves récentes livrées, pas estimées à partir d'un million de cas. Le fichier du modèle de corpus existe à plusieurs emplacements sous forme de copies de même taille. Le point de reprise contient plus d'état qu'un fichier de prédiction et n'est pas nécessaire pour utiliser simplement les poids.

Pour un réseau 128 → H → 1, le nombre de paramètres vaut `128×H + H + H + 1 = 130×H + 1`. Le volume de poids dépend donc surtout de la largeur du réseau. Il ne croît pas proportionnellement au nombre d'exemples. Les métadonnées de provenance peuvent croître, et les versions successives conservées occupent un espace cumulatif.

Les neuf fichiers `experience/models/model-000001.json` à `model-000009.json` totalisent **444 623 octets**. Ce sont neuf sauvegardes successives du même type de guide, pas neuf spécialistes de géométries indépendants. Les anciens guides interactifs v1 de 16 entrées et 217 paramètres apparaissent encore dans les preuves rc3 : ils ne doivent pas être confondus avec le schéma v2 de production.

L'état final des démonstrations récentes garde le classement neuronal **désactivé** (`deploy_enabled=false`) : aucun gain de validation suffisant n'est démontré. Cela n'empêche pas la génération classique ni la recherche de recettes et leur réemploi revalidé. Envoyer ces fichiers à une autre personne ne transforme pas ces essais en modèle industriel déjà validé.

## Corpus : taille réelle et extrapolation au million

Le plan cible du rapport livré est de **250 000 cas structurés + 750 000 aléatoires**. Il n'a encore exécuté aucun de ces cas. Ses trois fichiers de métadonnées représentent seulement **5 058 octets**, d'après `verification/corpus/summary.json`. Définir un plan d'un million ne remplit pas le disque avec un million de pièces.

Le pilote réellement exécuté contient **48 configurations**, réparties sur 24 familles, et **96 résultats de stratégies**. Il a produit un maillage dans 46 cas ; 45 cas disposent d'au moins une stratégie admissible. Ses segments JSONL récents représentent **666 890 octets**, soit **13 893,54 octets par configuration en moyenne**, avec deux stratégies par configuration et sans conservation de tous les maillages.

| Hypothèse strictement proportionnelle à ce pilote | JSONL estimé seul |
| --- | ---: |
| 1 000 configurations | 13,89 Mo |
| 10 000 configurations | 138,94 Mo |
| 100 000 configurations | 1,389 Go |
| 250 000 configurations | 3,473 Go |
| 750 000 configurations | 10,420 Go |
| 1 000 000 configurations | **13,894 Go / 12,94 Gio** |

Cette extrapolation est un **ordre de grandeur**, pas une limite garantie : le pilote est petit, ses pièces sont simples, ses cas invalides ont des lignes plus courtes et il ne teste que deux stratégies par cas. Le plan habituel prévoit quatre stratégies ; les lignes seront alors plus longues, sans qu'on puisse multiplier exactement par deux puisque les données communes à la configuration ne sont pas dupliquées de la même façon. Pour dimensionner une vraie campagne, mesurer un lot représentatif de plusieurs centaines ou milliers de cas avec les réglages définitifs.

Le dossier complet du pilote occupe **1 298 409 octets**, dont les segments 666 890 et les trois fichiers de modèle/reprise 622 158. Ajouter les copies de modèles installées dans les espaces de travail, les journaux, rapports et fichiers temporaires au budget d'une campagne. L'entraînement lit les segments par mini-lots ; cela évite de charger tout le corpus en mémoire, mais pas son stockage ni ses relectures.

### Si tous les maillages sont conservés

L'option `retain_meshes` change fortement le budget. Le rapport de mesure rc5 `verification/optimization/bulk.json` utilise un cas de 45 183 tétraèdres pour lequel les artefacts lourds représentent environ **10,40 Mo par évaluation**. À titre purement arithmétique, un million de cas × quatre stratégies × 10,40 Mo représente **41,6 To**, hors inspections de référence, corpus, copies et journaux. Ce scénario n'est pas une prévision de la taille des futures pièces.

La configuration normale d'une campagne ne conserve pas tous ces maillages. Elle garde les paramètres, caractéristiques, qualités et échecs nécessaires à l'apprentissage. Une stratégie de conservation sélective des cas difficiles et des résultats de référence est plus adaptée à une campagne longue.

## Taille de la mémoire interactive

Le dossier de démonstration récent `verification/continuous_learning/continuous_runs/20261104T091837738488/experience/` occupe **1 428 396 octets** pour six géométries, neuf contextes et 29 observations/journalisations :

| Contenu | Octets |
| --- | ---: |
| `events.jsonl` : historique détaillé des essais | 593 472 |
| `memory.json` : index des recettes et contextes | 307 817 |
| `model.json` : guide courant | 49 964 |
| `models/` : neuf versions | 444 623 |
| `sources/` : source STEP conservée | 32 520 |
| Total | **1 428 396** |

La taille d'une source CAO importée peut dépasser largement cet exemple. Répéter une pièce ne supprime pas la nécessité de conserver le journal des essais. Les limites documentées de l'index interactif sont 1 000 contextes, 32 000 observations et 128 Mio ; celle du journal est 512 Mio. Elles ne plafonnent pas les octets de tous les maillages, sources ou modèles. Une limite atteinte déclenche une erreur explicite, sans purge automatique. Le corpus segmenté constitue un mécanisme distinct, prévu pour de plus grands volumes.

## Ce qu'il faut envoyer à une autre personne

| Besoin du destinataire | Contenu nécessaire |
| --- | --- |
| Utiliser le logiciel | Sources et ressources du projet, installation des dépendances et binaire compatible avec son système, ou compilation locale |
| Utiliser un guide de corpus entraîné | JSON complet du guide compatible, avec son schéma et ses métriques ; dans son espace de travail : `models/corpus.json` |
| Utiliser le guide historique | JSON compatible dans `models/guide.json`, selon les limites de domaine du guide |
| Reprendre l'expérience sur les mêmes pièces | Dossier `experience/` cohérent : index, journal, sources et guide, en respectant les noms et empreintes |
| Reprendre un entraînement de corpus | Segments, manifeste, progression et modèle/point de reprise compatibles ; conserver les jeux de validation et test |
| Échanger une pièce et ses réglages | Projet portable `.amp` et fichiers nécessaires selon son mode de sauvegarde |
| Faire tourner un calcul mécanique | Maillage exporté, groupes et unités contrôlés, plus le modèle mécanique propre au solveur |

Arrêter les calculs qui écrivent avant de copier un dossier d'expérience ou un corpus. Copier un ensemble cohérent dans un nouvel espace de travail évite de remplacer un historique existant par accident. Un guide courant pèse ici environ **50 ko** ou **126 ko** ; transmettre tout le corpus pour une simple prédiction n'est pas nécessaire. Pour un usage reproductible, joindre la version du logiciel, le schéma de caractéristiques, les réglages, la provenance des données et les métriques. Conserver `deploy_enabled` et ses conditions d'activation : ne pas forcer ce champ pour contourner la validation.

Les données de mémoire et de corpus peuvent contenir les géométries, noms de pièces et réglages de leurs auteurs. Vérifier le contenu choisi avant une diffusion publique. Un dépôt GitHub peut contenir le code et la documentation ; les gros corpus et campagnes se distribuent séparément. Cette rc5 doit aussi être accompagnée d'une licence explicite et du bilan de ses dépendances avant d'annoncer les droits de redistribution ; voir [THIRD_PARTY.md](THIRD_PARTY.md).

### Produire un petit paquet de poids vérifiable

`scripts/share_model.py` crée un ZIP contenant le JSON du modèle, un guide d'installation et un manifeste d'empreintes SHA-256. Il fonctionne avec la bibliothèque standard Python, sans Gmsh ni interface. Les trois types sont `historique`, `experience` et `corpus` :

```sh
python3 scripts/share_model.py --kind corpus \
  --model MON_CORPUS/models/current.json \
  --output output/mon_guide_corpus.zip

python3 scripts/share_model.py --kind experience \
  --model MON_ESPACE/experience/model.json \
  --output output/mon_guide_experience.zip

python3 scripts/share_model.py --kind historique \
  --model MON_ESPACE/models/guide.json \
  --output output/mon_guide_historique.zip
```

Le script refuse un fichier incomplet, un point de reprise présenté comme modèle, un ancien schéma incompatible, des poids non finis ou hors plage, des dimensions erronées et une activation contredisant les preuves déclarées. Un modèle valide mais désactivé peut être partagé : il reste désactivé. Ces contrôles de format et de cohérence ne sont pas une certification de performance ; le noyau Rust reste responsable de ses propres contrôles au chargement et de la validation de chaque maillage.

Le script ne joint aucun corpus, dossier mémoire ou fichier CAO séparé. Pour le guide historique, il retire également `training_configs`, champ qui peut embarquer les géométries d'entraînement et qui n'est pas requis par son chargeur de prédiction. Les poids, les preuves de validation et la décision d'activation sont conservés. Le manifeste signale ce retrait et garde l'empreinte du fichier source. L'option facultative `--report CHEMIN/rapport.json` joint un rapport choisi explicitement ; vérifier son contenu, car il peut contenir des noms et chemins locaux. Un fichier ZIP existant n'est jamais écrasé.

Les paquets de démonstration produits pendant l'audit sont disponibles sous `output/partage/`. Ils transportent les petits guides des essais livrés, tous désactivés dans leur état final. Pour le guide interactif, les poids seuls ne suffisent pas à reprendre l'historique cumulatif : les groupes de la mémoire d'origine doivent encore être présents dans les observations rejouées. Le paquet ne constitue donc pas un import de mémoire complète.

## Espace d'installation et mémoire de l'ordinateur

Les **74,32 Mo** décompressés ne comprennent pas l'environnement Python complet ni les caches Cargo. Qt/PySide6, Gmsh/OpenCASCADE, NumPy et les autres dépendances représentent un poste nettement plus gros que les scripts. Leur taille exacte dépend des versions, du système et des roues téléchargées ; la mesurer après installation avec `scripts/inventory_sizes.py` dans la catégorie des environnements. La compilation Rust crée `rust_core/target/`, distinct du binaire final. Les caches externes au dossier du projet ne sont pas mesurés par ce script.

La mémoire vive de **16 Go** du Mac M3 est largement supérieure aux poids des petits réseaux ci-dessus. La géométrie, les tableaux de nœuds/éléments, les contrôles de qualité et l'affichage sont les postes à mesurer sur de grosses pièces. La taille JSONL totale du corpus n'a pas à tenir dans la RAM grâce à la lecture progressive. Cela ne garantit pas qu'une pièce particulière de très grande taille tiendra en mémoire : mesurer le pic de mémoire et la durée sur des cas représentatifs avant une campagne longue.

Références internes : [rapport du corpus (archive complète)](provenance/PREUVES_EXTERNES.md), [apprentissage continu (archive complète)](provenance/PREUVES_EXTERNES.md), [optimisations rc5](OPTIMISATIONS_RC5.md), [guide du corpus et des langages](CORPUS_ET_LANGAGES.md), [limites](LIMITES.md).
