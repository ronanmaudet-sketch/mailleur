# Dépendances et licences

Pour les composants Rust et la bibliothèque standard liés au binaire M3,
consulter l'[inventaire Rust](THIRD_PARTY_RUST.md) et les notices originales
conservées dans `docs/licenses/`. La chaîne de compilation et ses caches ne
sont pas inclus dans la distribution.

Atelier Mailleur appelle des composants tiers installés dans son environnement
Python. Leurs sources, versions et notices restent distinctes de celles du
code spécifique de ce projet.

| Composant | Version demandée | Licence déclarée par le composant | Rôle |
| --- | --- | --- | --- |
| Gmsh | 4.15.2 | GPL version 2 ou ultérieure | CAO OpenCASCADE et génération du maillage |
| meshio | 5.3.5 | MIT | Lecture et écriture des formats de maillage |
| h5py | `>=3.10,<4` | BSD-3-Clause | Accès HDF5 pour MED |
| NumPy | `>=1.24,<3` | BSD-3-Clause, avec notices des composants embarqués | Tableaux et traitements numériques |
| Python | 3.11 à 3.13 | Python Software Foundation License et notices associées | Exécution de l'application |
| PySide6-Essentials / Qt et shiboken6 | `>=6.8,<7` ; 6.12.0 mesuré sur M3 | Conditions LGPL/GPL ou licence commerciale selon composant et distribution ; conserver les notices Qt | Interface QtCore, QtGui, QtWidgets |

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
- Qt for Python : https://doc.qt.io/qtforpython-6/licenses.html
- Contenu des paquets Qt : https://doc.qt.io/qtforpython-6/package_details.html

Gmsh demande également de citer le travail suivant lors de son utilisation
scientifique : C. Geuzaine et J.-F. Remacle, « Gmsh: a three-dimensional finite
element mesh generator with built-in pre- and post-processing facilities »,
International Journal for Numerical Methods in Engineering, 79(11), 2009,
1309–1331.

## Code propre au projet

Le titulaire a choisi, pour la rc6 du 7 novembre 2026, la **GNU GPL version 3
ou toute version ultérieure** (`GPL-3.0-or-later`) pour le code propre au projet.
Le texte officiel est dans [LICENSE](../LICENSE). Aucun nom d'auteur ou droit
sur les pièces d'entraînement n'est inventé par cette attribution de licence.
Les dépendances, traducteurs et logiciels propriétaires conservent leurs
licences et notices respectives ; SOLIDWORKS et les SDK ne sont pas inclus.
Cette notice est un inventaire technique, à conserver avec la diffusion.
