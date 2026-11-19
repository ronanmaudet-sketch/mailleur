# Convertir un document SOLIDWORKS en STEP

La passerelle s’exécute séparément de Mailleur. Elle ouvre une pièce
`.sldprt` ou un assemblage `.sldasm` dans SOLIDWORKS et lui demande d’écrire
un fichier `.step` ou `.stp`. Le STEP peut ensuite être transféré sur le Mac
et ouvert sur la page Géométrie de Mailleur.

Depuis le dossier du projet, sur un poste Windows équipé de SOLIDWORKS avec
licence, exécuter Windows PowerShell 64 bits en mode STA. Placer la pièce ou
l’assemblage et ses références dans des dossiers accessibles à SOLIDWORKS.
Choisir un nouveau fichier de destination.

```powershell
powershell.exe -NoProfile -STA -File .\scripts\converters\solidworks_to_step.ps1 `
  -InputPath 'C:\Pieces\support.sldasm' -OutputPath 'C:\Echanges\support.step'
```

L’option `-Configuration 'NomExact'` choisit la configuration de la pièce ou
de l’assemblage. Les composants supprimés dans la configuration provoquent un
refus par défaut. L’option `-AllowSuppressedComponents` autorise leur exclusion.
Une référence manquante ou un avertissement à l’ouverture doit être examiné.
Le script retourne un rapport JSON avec le résultat, la configuration et les codes
du logiciel. Le code de sortie zéro indique la création du STEP.

Sur le Mac, le parcours commence avec un STEP déjà exporté. L’import permet
l’inspection géométrique. La construction automatique de blocs structurés
directs à partir d’un STEP quelconque reste à développer. Pour essayer la
génération directe sur une pièce paramétrée compatible, utiliser
[l’exemple hexaédrique](../examples/direct_hex.json).

## Vérification de la passerelle

Le script `scripts/converters/test_solidworks_bridge.ps1` exerce neuf scénarios
de conversion avec une application simulée. Il couvre notamment la détection
d’une session occupée, les composants supprimés, la vérification du fichier de
sortie et la protection d’une destination existante. Les neuf scénarios ont réussi
sur le Mac arm64 le 19 novembre 2026 avec PowerShell 7.6.6. Le contrôle GitHub sous
Windows l’exécute à chaque mise à jour. L’essai avec un véritable poste
SOLIDWORKS et ses licences reste à effectuer. L’objectif 2020 et ultérieur
demande des essais sur les versions et assemblages des équipes concernées.

Les deux scripts PowerShell proviennent de la [révision publique précédant le
passage au moteur direct](https://github.com/ronanmaudet-sketch/mailleur/tree/942fe77c00022b9c254ef02d54865ae42a37e9a6/scripts/converters).
