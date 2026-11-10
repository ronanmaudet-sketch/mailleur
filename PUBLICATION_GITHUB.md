# Publication GitHub — 10 novembre 2026

Le dépôt public est créé :
[ronanmaudet-sketch/mailleur](https://github.com/ronanmaudet-sketch/mailleur).
Licence du projet : **GPL-3.0-or-later**.

Le code rc7, les tests, le guide, la note Cast3M et l'exemple réexécutable sont préparés dans ce dossier pour ce dépôt. Les archives vérifiées du 7 novembre restent inchangées.

## Récupérer une copie

Après l'envoi des fichiers sur la branche `main` :

```sh
git clone https://github.com/ronanmaudet-sketch/mailleur.git
cd mailleur
python3 scripts/install.py
```

Utiliser Python 3.11, 3.12 ou 3.13. L'installation et les prérequis par système sont décrits dans [README.md](README.md).

## Envoyer la préparation locale

Depuis la copie Git locale déjà préparée et validée, avec les droits d'écriture sur le dépôt :

```sh
git remote add origin https://github.com/ronanmaudet-sketch/mailleur.git
git push -u origin main
```

Si `origin` existe déjà, contrôler son adresse avec `git remote -v` ; pour la corriger, utiliser `git remote set-url origin https://github.com/ronanmaudet-sketch/mailleur.git`. Les contributions suivantes suivent le parcours habituel : branche, commit, push puis pull request.

## Contenu et contrôle

- `docs/cast3m/PROPOSITION_CAST3M.pdf` : note de cinq pages à transmettre.
- `docs/cast3m/PROPOSITION_CAST3M.docx` : version modifiable.
- `examples/cast3m_pilot/` : démonstrateur testé, avec mode de référence sans Mailleur installé.
- `docs/provenance/` : origine de la rc7, copies de preuves expurgées et reconstruction du binaire sans chemin personnel.
- `verification/publication_20261110/` : contrôles de cette préparation.

La CI multiplateforme fournie ne sera considérée comme exécutée qu'après une exécution GitHub Actions réussie. Aucun message n'a été adressé à l'équipe Cast3M au nom de l'auteur.
