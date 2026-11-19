# Dépendances et licences

Atelier Mailleur appelle des composants tiers installés dans son environnement
Python. Leurs sources, versions et notices restent distinctes de celles du
code spécifique de ce projet.

| Composant | Version demandée | Licence déclarée par le composant | Rôle |
| --- | --- | --- | --- |
| PySide6-Essentials / Qt | `>=6.8,<7` | LGPLv3, GPLv3 ou licence commerciale selon le composant | Interface de bureau |
| Gmsh | 4.15.2 | GPL version 2 ou ultérieure | CAO OpenCASCADE et génération du maillage |
| meshio | 5.3.5 | MIT | Lecture et écriture des formats de maillage |
| h5py | `>=3.10,<4` | BSD-3-Clause | Accès HDF5 pour MED |
| NumPy | `>=1.24,<3` | BSD-3-Clause, avec notices des composants embarqués | Tableaux et traitements numériques |
| Rust | Chaîne locale requise pour compiler | Licences du compilateur et des bibliothèques selon les composants | Compilation du noyau |
| Python | 3.11 à 3.13 | Python Software Foundation License et notices associées | Exécution de l'application |

La version exacte résolue des dépendances à plage doit être conservée dans le
rapport d'environnement de chaque livraison. Les roues binaires peuvent
embarquer des bibliothèques supplémentaires, avec leurs propres notices.
L'inventaire ci-dessus ne remplace pas ces notices complètes.

## Références

- Gmsh : https://gmsh.info/doc/texinfo/gmsh.html#Copying-conditions
- Licence complète Gmsh : https://gmsh.info/doc/texinfo/gmsh.html#License
- meshio : https://github.com/nschloe/meshio/blob/main/LICENSE.txt
- h5py : https://github.com/h5py/h5py/blob/master/LICENSE
- NumPy : https://numpy.org/doc/stable/license.html
- Python : https://docs.python.org/3/license.html

Gmsh demande également de citer le travail suivant lors de son utilisation
scientifique : C. Geuzaine et J.-F. Remacle, « Gmsh: a three-dimensional finite
element mesh generator with built-in pre- and post-processing facilities »,
International Journal for Numerical Methods in Engineering, 79(11), 2009,
1309–1331.

## Code propre au projet

Le code de Mailleur est diffusé sous la licence GNU GPL version 3 ou ultérieure. [Texte de la licence](../LICENSE)
