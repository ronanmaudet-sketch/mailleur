# Traction sur un bloc avec Mailleur et Cast3M

L’exemple part d’un [bloc de 2 × 1 × 1 mm](project.json). Le moteur direct génère 16 éléments HEX20, écrit le maillage UNV puis lance le calcul de traction dans Cast3M. Le contrôle compare les déplacements, la contrainte, les réactions, les groupes et les nombres de nœuds et d’éléments aux valeurs attendues.

Depuis le dossier installé avec son environnement Python activé, choisir un répertoire de sortie neuf et indiquer le chemin vers Cast3M.

```sh
python examples/cast3m_pilot/run_example.py --castem /chemin/vers/castem26 --output output/traction
```

Sur le Mac de vérification, le lanceur se trouve dans `/Applications/Cast3M/bin/castem26`. Un essai déjà généré peut être interprété à nouveau avec `--reference-directory output/traction` en choisissant une autre sortie. La rc9 possède aussi sa [référence historique](https://github.com/ronanmaudet-sketch/mailleur/tree/v1.0.0-rc9/examples/cast3m_pilot).

La sortie `resultat_cast3m.json` donne le statut de chacune des 43 comparaisons. La charge vaut 10 MPa et la réaction attendue vaut −10 N. Adapter les conditions et les références ensemble pour étudier une autre pièce. [Résultat mesuré en rc10](../../verification/rc10/resultat_cast3m.json) · [Modèle mécanique](mecanique.dgibi)

L’exemple est diffusé sous [GPL version 3 ou ultérieure](LICENSE). Cast3M s’installe séparément.
