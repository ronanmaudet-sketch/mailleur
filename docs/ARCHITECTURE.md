# Architecture native de la version rc6

La rc6 ajoute une couche d'entrée explicite : catalogue `import_registry.py`,
lecteurs de fichiers `imports.py`, processus isolé `import_worker.py`, puis
opération Rust `prepare_mesh_import` pour extraire, orienter et trianguler une
peau. Les transformations géométriques et la conversion des unités de 3MF
s'exécutent dans Rust ; Python décode et sérialise les données de fichier.
Les originaux restent conservés. Les formats propriétaires nécessitent une
passerelle locale configurée, distincte des lecteurs intégrés.
Voir le [guide public rc6](UTILISATION_ET_DEVELOPPEMENT.md) et les
[passerelles natives](IMPORTS_NATIFS.md).

Voir aussi le [guide complet](GUIDE_COMPLET_MAILLEUR.md), la
[documentation API](API_UTILISATION.md) et les [mesures Apple M3](MESURES_M3.md).
Les contrats numériques introduits dans la rc4 restent applicables à la rc5.

## Répartition des responsabilités

| Couche | Technologie | Responsabilité |
| --- | --- | --- |
| Interface de bureau | Python / PySide6 | Trois pages, saisie des paramètres, unités affichées, sélection d'entités, lancement des opérations, lecture des rapports |
| Pont natif | Python, module `atelier.native` | Localiser le binaire, sérialiser les demandes, démarrer le processus Rust et décoder sa réponse |
| Noyau de l'application | Rust, `rust_core/` | Valider les paramètres, appliquer les conversions et transformations, préparer les entités, piloter le maillage, le remaillage et les contrôles numériques |
| Bibliothèque géométrique et maillage | Gmsh / OpenCASCADE, code natif C/C++ | Calculs CAO et algorithmes de maillage exposés par l'API C appelée depuis Rust |
| Fichiers et profils | Couche d'entrée/sortie et profils solveur | Sérialisation et représentation des résultats ; les transformations numériques restent confiées au noyau |

Le noyau est une application Rust qui charge la bibliothèque native Gmsh et
utilise son interface C. Cela ne signifie pas que les algorithmes internes
OpenCASCADE ou Gmsh ont été réécrits en Rust : ils restent exécutés par leur
bibliothèque C/C++.

Le parcours de production ne délègue pas ses opérations géométriques,
conversions de coordonnées, génération ou contrôles de qualité au prototype
Python. Le pont Python transporte des données et des commandes ; l'interface
les présente et recueille les choix de l'utilisateur.

## Trois pages

**Géométrie** regroupe la source, ses unités, les primitives et transformations,
les diagnostics et les entités sélectionnables. La source initiale doit être
conservée afin de distinguer une réparation ou transformation de son entrée.

**Maillage** regroupe la portée des opérations, les tailles globales/locales,
les correspondances d'entités, les options de remaillage et les rapports de
qualité. La sélection affichée doit identifier les entités utilisées par le
noyau ; une sélection visuelle sans correspondance géométrique n'est pas une
preuve d'application du réglage.

**Export** regroupe l'unité de sortie, les groupes et le profil du solveur.
Une conversion d'unité doit modifier les données numériques dans le noyau et
être enregistrée dans le rapport. Les profils de fichiers ne définissent pas
à eux seuls un modèle physique de calcul.

## Remaillage partiel

Le mode strict est défini par la conservation des nœuds et éléments hors de
la portée choisie. La frontière entre parties conservées et remaillées fait
partie de cette contrainte. Une opération qui exige de modifier cette
frontière doit être refusée ou annoncée comme un mode différent.

Les preuves de conservation doivent comparer le fichier avant/après :
identifiants ou correspondances, coordonnées, connectivité et groupes. La
stabilité visuelle seule ne suffit pas. Les limites réelles du mode sont
énumérées dans le rapport de validation de la livraison.

## Processus et dépendances

Le processus graphique et le noyau sont séparés. Le chemin explicite
`ATELIER_CORE` permet de sélectionner le noyau. Sans cette variable, le pont
cherche le résultat de la compilation Cargo dans `rust_core/target/release/`,
puis un binaire de la plateforme dans `rust_core/bin/`.

La bibliothèque Gmsh est détectée parmi les fichiers installés ;
`GMSH_LIBRARY` permet d'en préciser un chemin explicite. Le noyau, la
bibliothèque et le système doivent partager une architecture compatible.
Les fichiers `Cargo.toml` et `Cargo.lock` sont fournis pour reconstruire le
noyau à partir de ses sources.

## Recherche et apprentissage continu

Le parcours d'apprentissage de la pièce courante est une opération native
Rust distincte du conseiller historique. Le noyau construit les recettes,
appelle le même backend géométrique que la génération classique, compare les
rapports de qualité et conserve la meilleure solution admissible. Le pont
Python ne calcule ni la qualité, ni les récompenses, ni les poids du réseau.

L'identité de l'expérience tient compte de la source et du contexte de
maillage ; un nom de fichier identique n'est pas une preuve d'identité de
contenu. Les paramètres de qualité et les zones critiques font partie du
contexte. Une recette réutilisée est exécutée à nouveau et contrôlée.

La mémoire persistante distingue le journal `events.jsonl` de l'index
`memory.json`, qui conserve le dernier état des recettes. L'index est borné à
1 000 contextes, 32 000 observations et 128 Mio. Les versions du modèle sont
conservées séparément. Le réseau de mémoire comporte 128 entrées, 12 neurones
cachés et une sortie (1 561 paramètres), distinct du guide historique de
147 paramètres.

Le modèle entraîné
sur l'historique prédit l'intérêt des recettes pour les classer, tandis que les
règles de qualité contrôlent leur admissibilité. Une bonne prédiction ne
permet pas de contourner un échec de génération ou une violation de zone.

Les contrôles spatiaux sont calculés sur les éléments complets, indépendamment
du sous-échantillonnage éventuel de l'aperçu. Ils distinguent le seuil global,
le nombre et la sévérité des mauvais éléments et la qualité exigée dans chaque
zone critique. Les contraintes physiques ne sont pas déduites de la géométrie.

La conservation des expériences et l'entraînement incrémental sont deux
opérations différentes. L'historique permet de rejouer les acquis lors d'une
actualisation du réseau ; les rapports décrivent la quantité de données
réellement utilisée et les éventuelles limites de validation.

Voir [APPRENTISSAGE_CONTINU.md](APPRENTISSAGE_CONTINU.md) pour le parcours et les
champs JSON.

## Corpus segmenté et entraînement par mini-lots

Le pipeline `corpus_*` est distinct de la mémoire interactive. Le plan par
défaut décrit 250 000 cas structurés et 750 000 cas aléatoires sans les
calculer. Les opérations de génération, de calcul des stratégies, de
segmentation JSONL et de reprise s'exécutent dans Rust. L'interface ne fait que
demander un lot explicitement borné et afficher ses compteurs.

La représentation commune à 128 entrées ajoute des informations spatiales
d'entités, de frontières et de zones protégées aux paramètres globaux et
topologiques. Elle décrit des empreintes normalisées, pas une occupation CAO
exacte ni un réseau de graphes.

L'entraîneur du corpus lit séquentiellement les segments et charge des
mini-lots bornés. Les familles d'entraînement, de validation et de test sont
disjointes. Le jeu de test est évalué après le choix des poids et de leur
activation. Les rapports séparent l'erreur de prédiction et la qualité de la
stratégie effectivement sélectionnée.

Le modèle de corpus est distinct du petit réseau de mémoire interactive. Ses
dimensions, ses points de reprise et ses critères d'activation figurent dans
son rapport ; un fichier de poids présent ne prouve pas un gain de qualité.
Voir [CORPUS_ET_LANGAGES.md](CORPUS_ET_LANGAGES.md).

## Code historique

Les anciens noms de modules Python sont des adaptateurs vers le processus
Rust ou des API retirées avec un diagnostic explicite. Ils ne contiennent
plus un moteur de géométrie, de maillage ou d'apprentissage alternatif. Les
tests retirés du prototype sont conservés comme références textuelles et ne
sont pas comptés dans la suite exécutée. Les tests de compatibilité vérifient
les nouveaux contrats ; les tests natifs vérifient les calculs.
