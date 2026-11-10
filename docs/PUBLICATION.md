# Préparer une publication GitHub de Mailleur

Ce document décrit une préparation locale. Il ne crée pas de dépôt distant et ne publie pas de fichiers. Le titulaire a choisi la **GNU GPL version 3 ou toute version ultérieure** pour le code propre au projet.

## État et périmètre à annoncer

La révision rc6 ajoute des lecteurs et une passerelle locale de conversion, ainsi que des exports de sous-ensembles structurels. Le catalogue `python -m atelier formats --json` expose les capacités de la version installée. Présenter séparément :

- les formats intégrés et leurs éléments réellement acceptés ;
- les formats conditionnels, qui demandent un convertisseur configuré et éventuellement un logiciel propriétaire ;
- les vérifications de fichiers et tests effectivement exécutés ;
- les essais encore nécessaires dans les versions des logiciels cibles.

La cible 2020+ n'est pas une certification universelle. Un lecteur Nastran de maillage n'est pas un parseur complet de tout jeu de commandes Nastran ; un `.key` ou un `.apdl` de maillage n'est pas un calcul mécanique complet. La passerelle SOLIDWORKS est conditionnelle : ses neuf scénarios de protocole avec doubles ont été exécutés sous PowerShell sur Mac, mais aucune conversion avec un SOLIDWORKS réel n'a été validée. Les résultats historiques Linux et Apple M3 du 4 novembre 2026 doivent conserver leur date et leur révision d'origine.

## Licence retenue : GPL-3.0-or-later

Le code propre au projet est distribué sous la **GNU GPL version 3 ou toute version ultérieure**, selon le choix explicite du titulaire. Le texte officiel figure dans [LICENSE](../LICENSE) et l'identifiant de licence est `GPL-3.0-or-later`. Conserver cette information et le texte de licence dans les distributions. Aucun nom d'auteur, année de création ou détenteur de copyright n'a été inventé pour remplir une notice.

Le choix de la licence du code personnel ne remplace pas les obligations applicables à Gmsh, OpenCASCADE, Qt/PySide et aux autres bibliothèques. Examiner [THIRD_PARTY.md](THIRD_PARTY.md) et les notices des versions effectivement distribuées. Un paquet source qui installe les dépendances et une application qui embarque leurs binaires n'ont pas le même contenu à inventorier. Les traducteurs commerciaux et SOLIDWORKS ne sont pas distribués par la passerelle fournie.

Traiter aussi les droits sur les données : exemples CAO, fichiers natifs, pièces incluses dans les assemblages, jeux d'entraînement et poids. Une licence du programme n'autorise pas à publier une pièce confidentielle ou le corpus d'un tiers. Les `.cadpack` et dossiers d'expérience peuvent contenir bien plus que le seul nom du projet.

## Contenu du dépôt

Inclure les éléments nécessaires pour comprendre, installer, tester et reproduire les exemples publics :

| À conserver | Pourquoi |
| --- | --- |
| `README.md`, documentation et notices | Installation, architecture, formats, API, limites et contribution |
| `atelier/`, `mailleur/`, `rust_core/src/` | Sources de l'interface, du transport, des conversions et du noyau |
| `pyproject.toml`, `requirements.txt`, Cargo manifest et verrou | Dépendances et versions reproductibles |
| Lanceurs et `scripts/` | Installation, diagnostics, mesures et outils de distribution |
| `tests/` et leurs fixtures publiques | Vérification des contrats et cas limites |
| `examples/` partageables | Première génération et usage reproductible |
| `.github/workflows/` | Définition des vérifications automatisées |
| Preuves sélectionnées de `verification/` | Origine des résultats annoncés et fixtures utilisées par certains tests |

Ne pas supprimer intégralement `verification/` sans examiner les dépendances des tests : des exemples de modèles et fichiers de validation y servent de fixtures. On peut alléger les historiques de campagnes dans une release, à condition de conserver les preuves citées et les fichiers nécessaires aux tests annoncés. Expliquer le contenu exact de l'archive.

Exclure les environnements `.venv`/`.runtime`/`.bootstrap`, la chaîne locale `.toolchain`, les caches Cargo et Python, `target/`, les travaux de `output/`, les corpus de production et les CAO privées. Le `.gitignore` est une aide ; il ne peut pas déduire quels fichiers placés sous un autre nom sont confidentiels. Examiner également les PDF, captures et rapports générés pour leurs noms de fichiers et chemins locaux.

L'outil `python scripts/inventory_sizes.py` sépare sources, preuves et postes créés par l'installation. Un corpus ou environnement de plusieurs gigaoctets n'a pas à entrer dans l'historique Git courant pour permettre l'installation du programme.

## Vérifier une extraction propre

Installer et tester depuis une copie ou une extraction neuve avec les dépendances documentées. Le fait que le dossier de développement fonctionne ne prouve pas que tous ses fichiers nécessaires sont présents dans le paquet.

Exécuter les vérifications correspondant au contenu annoncé : compilation Rust avec verrou Cargo, tests, diagnostic, génération d'un exemple, import étendu, export de sous-ensemble et relecture. Pour un format conditionnel, garder un résultat « convertisseur absent » clair et tester séparément le parcours réel lorsque son logiciel est disponible.

Enregistrer version du projet, système/architecture, versions de Python, Rust, Gmsh et bibliothèques, commandes, nombres de tests, éventuels tests ignorés et résultat. Pour les performances, conserver les mesures brutes et séparer préparation, compilation, traduction, maillage, qualité et export. Ne pas recopier les chiffres d'une autre machine dans un rapport local.

La CI définie dans [tests.yml](../.github/workflows/tests.yml) est une matrice Linux/macOS/Windows avec Python 3.11 et 3.12. Une coche de workflow et un compte de tests doivent provenir d'une exécution réelle de cette version. Les plateformes sans logiciel propriétaire ne peuvent certifier sa passerelle par un simple test simulé.

## Préparer un premier commit local

Si le dossier n'est pas encore suivi par Git, initialiser un dépôt local après examen des fichiers. Ces commandes sont des instructions à adapter au dossier de travail ; elles ne publient rien :

```sh
git init
git status --short
git diff --stat
```

Ajouter ensuite explicitement les fichiers publics retenus, puis examiner `git diff --cached --stat` et `git diff --cached` avant le commit. Vérifier les nouvelles sources, les binaires éventuels et les données de validation, plutôt que d'ajouter indistinctement tout le dossier de développement. Si des fichiers ont déjà été suivis, les nouvelles règles d'exclusion ne les retirent pas de l'historique.

Créer le dépôt distant, définir sa visibilité et pousser le commit constituent une étape séparée, à effectuer après examen du contenu et des droits sur les données. La préparation de cette révision n'a pas créé de dépôt GitHub ni transmis de CAO à un service de publication.

## Préparer une release

Le script `scripts/package_native_release.py` prépare une archive source et son manifeste d'intégrité selon les règles du projet. Vérifier sa sortie, les rapports qu'il embarque et le binaire propre à la plateforme. Un binaire macOS arm64 ne fonctionne pas directement sous Windows ou Linux ; les sources peuvent être compilées sur le système cible.

Une release devrait contenir : le numéro de révision exact, ce qui a changé, la liste des formats avec leur niveau de validation, les prérequis et commandes de démarrage, les résultats effectivement obtenus, les limites, les notices et les empreintes des artefacts. Une archive d'intégrité permet de détecter une altération ; elle ne prouve pas à elle seule l'identité d'un auteur.

Les PDF et documents Word se reconstruisent séparément du moteur, avec les dépendances facultatives de documentation :

```sh
python -m pip install -r requirements-docs.txt
python scripts/build_guide.py --public-release --check-sources
python scripts/build_guide.py --public-release
```

Le guide rc7 assemble cinq sources, dont `VALIDATION_RC7.md` obligatoire, et calcule le nombre de chapitres/annexes. Les sorties `Mailleur_Guide_public_rc7.pdf` et `.docx`, le relevé de contrôle textuel et le manifeste d'empreintes sont créés dans `docs/livrables/`, avec des noms distincts des anciens guides rc5/rc6. Le générateur utilise les polices installées ou le repli Vera/Courier fourni par les outils, sans télécharger de polices. `--fonts DOSSIER` permet de choisir un dossier compatible. Pour l'ancienne collection, `--historical-rc5 --source-directory DOSSIER_ARCHIVE` demande des sources historiques conservées ; l'option ne restaure pas d'anciennes versions de fichiers.

Distribuer un guide appris avec `scripts/share_model.py` si le destinataire n'a pas besoin du corpus. Ce paquet conserve les métadonnées et l'état d'activation, sans importer automatiquement la mémoire ni ses sources. Pour une mémoire interactive, les poids seuls ne suffisent pas à reprendre un historique cumulatif sur un espace neuf. Pour un corpus, indiquer la provenance et les jeux de validation/test, sans annoncer qu'un million de cas a été exécuté parce que le plan le prévoit.

## Recevoir et examiner les contributions

Demander un exemple partageable, la commande, les unités, les versions, le rapport obtenu et le résultat attendu. Pour une conversion propriétaire, demander la version du logiciel et l'état de ses références, sans exiger une pièce dont le partage est interdit. Favoriser une reproduction synthétique lorsqu'une géométrie est confidentielle.

Une proposition doit expliquer le comportement après correction et ses vérifications. Comparer les connectivités, groupes et unités, en plus du rendu visuel. Une optimisation ne doit pas effacer un avertissement, accepter un élément refusé ou prétendre transférer des données physiques absentes. Les ajouts de formats, de traducteurs et de commandes doivent être explicites dans le catalogue et la documentation.

Voir [UTILISATION_ET_DEVELOPPEMENT.md](UTILISATION_ET_DEVELOPPEMENT.md) pour les responsabilités du code et la méthode de mesure, et [CONTRIBUER.md](CONTRIBUER.md) pour les règles de contribution existantes.
