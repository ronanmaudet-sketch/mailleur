# Exemples de prise en main

Les géométries synthétiques livrées servent à vérifier le parcours, sans
constituer une validation mécanique industrielle.

Avec le Python de l'environnement du projet :

```sh
python -m atelier mesh examples/atelier_plate.json -o output/plaque --formats msh,inp
python -m atelier mesh examples/import_maillage.json --source examples/import_tetra.obj \
  -o output/tetra_obj --formats msh,inp,med,unv,cast3m,nastran,lsdyna,ansys_structural
python -m atelier mesh examples/import_maillage.json --source examples/import_tetra.3mf \
  -o output/tetra_3mf --formats msh,nastran
```

Le projet JSON décrit les objectifs et unités ; `--source` fournit le fichier
à lire. OBJ et 3MF décrivent ici la même peau tétraédrique de 2 mm de côté
sur les axes. Le 3MF déclare lui-même son unité. Le nouveau maillage est
calculé et contrôlé, puis exporté ; les éléments et réglages physiques sont
à compléter dans le solveur.

Pour une CAO importée plus générale, consulter `atelier_import.json` et le
STEP `assemblage_deux_volumes.step`. Les projets `.amp` regroupent un projet
et sa source. Les sources natives propriétaires requièrent la passerelle
décrite dans [IMPORTS_NATIFS.md](../docs/IMPORTS_NATIFS.md).
