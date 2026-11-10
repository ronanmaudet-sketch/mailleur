# Contribuer à Atelier Mailleur

Ce guide concerne le code rc5 Rust + Python/Gmsh. L'ancienne description
« Mailleur v2.5 » avec GNN/PPO et moteur Rust autonome décrit un autre projet :
elle ne constitue pas la spécification de cette version.

## Préparer une modification

Installer Python 3.11 à 3.13, Rust/Cargo, puis les dépendances selon le
[README](../README.md). Travailler sur une branche dédiée et conserver un cas
minimal qui reproduit le problème : géométrie partageable, unités, paramètres,
graine, résultat attendu et rapport observé. Pour une pièce confidentielle,
fournir une géométrie synthétique équivalente.

Python porte l'interface et les échanges de fichiers ; les calculs de
géométrie, maillage, qualité et apprentissage relèvent de Rust et de
Gmsh/OpenCASCADE. Une correction de l'affichage ne doit pas devenir une
seconde implémentation du moteur numérique en Python. Voir
[l'architecture](ARCHITECTURE.md) et [l'API](API_UTILISATION.md).

## Vérifier et mesurer

Depuis la racine, avec l'environnement Python du projet activé :

```sh
cargo test --locked --manifest-path rust_core/Cargo.toml
cargo build --locked --release --manifest-path rust_core/Cargo.toml
python -m atelier doctor
python -m unittest discover -s tests -v
```

Pour une vue Qt sans écran, définir `QT_QPA_PLATFORM=offscreen` avant les tests.
La matrice GitHub Actions est décrite dans
[tests.yml](../.github/workflows/tests.yml) ; un fichier de CI présent n'est
pas la preuve d'une exécution réussie. Les tests ciblés viennent d'abord ;
après une modification du noyau ou du protocole, exécuter les suites Rust et
Python et les parcours de génération, reprise et export concernés.

Comparer les performances en **release**, sur la même machine, avec la même
version de Gmsh, les mêmes données et les mêmes réglages. Séparer compilation,
préparation, génération, contrôle qualité, entraînement, transport et rendu.
Conserver les mesures brutes, au moins plusieurs répétitions et leur médiane ;
consigner l'environnement et la taille des entrées. Les scripts
`scripts/benchmark_*.py` fournissent des scénarios reproductibles.

Une optimisation doit conserver les unités, groupes, correspondances,
connectivités attendues et critères de qualité. Aucun élément inversé ou
dégénéré ne devient admissible pour gagner du temps. Contrôler le minimum de
qualité, le nombre d'éléments médiocres et chaque zone protégée, au-delà de la
seule moyenne. Si un algorithme change les résultats numériques, le déclarer
et comparer explicitement les critères sur les mêmes cas ; ne pas présenter
une simple ressemblance visuelle comme une équivalence.

Les mesures synthétiques du petit réseau ne représentent ni une campagne CAO
réelle ni un gain de qualité mécanique. Les résultats historiques de livraison
restent séparés des nouvelles mesures locales.

## Préparer GitHub et une proposition de modification

Avant le premier dépôt, choisir avec le titulaire des droits la licence du
code propre au projet et ajouter le fichier `LICENSE` correspondant. Aucune
licence n'est choisie automatiquement ici. Examiner aussi les notices des
dépendances et les composants binaires redistribués dans
[THIRD_PARTY.md](THIRD_PARTY.md).

Vérifier la liste des fichiers à inclure et le diff. Le `.gitignore` exclut les
environnements, compilations et dossiers de travail habituels ; des données
placées ailleurs doivent être examinées séparément. Conserver les exemples
publics et les preuves utiles, et distribuer les poids entraînés séparément
avec leur provenance. Pour préparer une proposition, décrire le problème, le
comportement après correction, les vérifications et les limites restantes.
La préparation locale n'effectue aucune publication sur GitHub.

## Priorités d'amélioration

1. **Établir une référence réaliste.** Ajouter des pièces jamais vues, des
   imports CAO, des zones critiques, des cas volumineux et des échecs attendus.
   Mesurer qualité, taux d'acceptation, temps et mémoire, avec et sans guide.
2. **Améliorer la diversité du corpus.** Répartir les paramètres et cas rares
   entre familles, éviter les doublons et garder des familles réservées à la
   validation et au test. Le million planifié est un budget de cas ; ce nombre
   ne démontre ni diversité suffisante ni généralisation.
3. **Optimiser le poste réellement coûteux.** Profiler les phases Rust/Gmsh et
   les écritures avant de modifier le réseau. Étudier les réutilisations de
   données, traitements par lots et processus indépendants sous budget mémoire,
   avec validation des reprises et du déterminisme.
4. **Évaluer le classement appris.** Le MLP classe des recettes ; Gmsh génère
   toujours le maillage. Garder l'activation conditionnée à un gain validé.
   Enrichir la représentation ou changer d'architecture seulement si des cas
   mesurés montrent une limitation : accélérer le classifieur n'accélère pas
   nécessairement le maillage complet.
5. **Étendre les capacités avec des preuves.** Valider les exports dans les
   solveurs cibles, puis envisager une boucle calcul–estimation d'erreur–adaptation.
   Le tout-hexa automatique et un guide GNN/PPO exigeraient des développements
   et validations distincts ; ils ne sont pas des fonctions de la rc5.

