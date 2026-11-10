# Import des fichiers CAO natifs et passerelle SOLIDWORKS

État du 7 novembre 2026. La passerelle fournie appelle les API officielles SOLIDWORKS pour produire un STEP que le noyau Mailleur peut ensuite importer. **Elle nécessite Windows et une installation SOLIDWORKS compatible ; elle n'est pas un lecteur `.sldprt` ou `.sldasm` intégré au moteur.** Son code n'a pas été exécuté sur une installation SOLIDWORKS réelle dans cet audit effectué sur Mac.

La campagne rc7 a réellement exercé STEP/STP, BREP, IGES/IGS et plusieurs
maillages ouverts sur des solides décentrés, avec ordres 1/2 et unités m/mm.
Pour IGES, Gmsh 4.15.2 initialise tardivement le registre d'unités OpenCASCADE :
Mailleur effectue une lecture d'initialisation dans un modèle transitoire jeté,
puis la lecture réelle avec l'unité cible explicite. Ce contournement ajoute
une seconde lecture ; les unités du document restent interprétées par OCC.
Les en-têtes IGES en mètres et millimètres ont été contrôlés. Le détail et les
limites des essais sont dans [VALIDATION_RC7.md](VALIDATION_RC7.md).

L'objectif de compatibilité concerne les logiciels de CAO à partir de leurs versions 2020. Ce périmètre constitue une cible de validation par éditeur, version et type de document, et non la garantie que tous leurs formats sont décodés. Un ancien logiciel de CAO n'est pas supposé ouvrir les fichiers natifs de toutes les versions ultérieures. Une lecture partielle en mode « future version » n'est pas une validation de traduction géométrique complète.

## Choisir le chemin d'import

| Source | Chemin vers Mailleur | Dépendance supplémentaire |
| --- | --- | --- |
| STEP/STP, IGES/IGS, BREP | Import géométrique du moteur existant | Gmsh/OpenCASCADE livré par l'installation |
| Fichier de maillage ou de visualisation | Lecteur du format et reconstruction surfacique selon les restrictions documentées | Lecteur disponible et type d'élément pris en charge ; voir `FORMATS.md` |
| SOLIDWORKS `.sldprt` | Convertisseur configuré → STEP → import CAO | SOLIDWORKS Windows desktop et PowerShell 64 bits STA |
| SOLIDWORKS `.sldasm` | Même chaîne, avec toutes les références accessibles et la bonne configuration | Installation SOLIDWORKS compatible ; assemblage complet ou exclusions explicites |
| Autre format CAO natif | Convertisseur externe explicitement configuré → format géométrique intermédiaire | Logiciel de l'éditeur ou traducteur compatible, à valider |
| Fichier propriétaire sans convertisseur | Export STEP depuis son logiciel d'origine, puis import STEP | Aucune prise en charge native n'est annoncée en son absence |

Un format reconnu dans une liste d'extensions peut donc être **conditionnel**. La présence de son nom ne prouve pas que le traducteur, la licence ou les références nécessaires sont disponibles. Le parcours doit signaler la conversion utilisée et refuser une sortie vide ou illisible. Une conversion d'un fichier de résultats éléments finis en surface n'importe pas automatiquement ses lois de matériaux, contacts ou chargements.

## Ce que fournissent OpenCASCADE et FreeCAD

OCCT fournit des lecteurs/écrivains d'échange dont STEP, IGES et BREP ; ajouter un moteur OCCT ne suffit pas à démontrer la lecture universelle des formats natifs d'autres éditeurs. Les composants libres et les produits de traduction commerciale d'Open Cascade doivent être distingués : par exemple, les notes du produit séparé CAD Processor décrivent l'ajout de formats propriétaires. Cela ne prouve pas qu'ils soient présents dans la bibliothèque OCCT utilisée par Gmsh. [Documentation OCCT](https://dev.opencascade.org/doc/occt-6.9.0/overview/html/), [notes de CAD Processor](https://opencascade.com/release_notes/cad-processor/).

FreeCAD annonce son format FCStd et de nombreux formats d'échange, notamment STEP, IGES et plusieurs maillages ; ses extensions peuvent ajouter d'autres formats. Une installation FreeCAD appropriée peut donc constituer une passerelle FCStd → STEP ou convertir les formats qu'elle lit réellement. Sa présence ne constitue pas à elle seule une preuve de prise en charge complète de `.sldprt`, CATPart, NX, Inventor ou Creo dans toutes leurs versions. Aucun convertisseur FreeCAD universel n'est fourni ou prétendu validé ici. [Formats annoncés par FreeCAD](https://www.freecad.org/features).

Pour préserver une géométrie précise de solide, préférer le STEP produit par le logiciel d'origine plutôt qu'un STL de visualisation. Les deux représentations n'offrent pas les mêmes informations : une peau triangulée ne contient pas l'arbre paramétrique ni toutes les surfaces exactes d'origine.

## Contrat du convertisseur configurable

Le fichier pointé par la variable `MAILLEUR_CAD_CONVERTERS` décrit les programmes locaux autorisés par la configuration de l'utilisateur. La commande est une **liste d'arguments**, et non une chaîne à exécuter dans un shell. `{input}` désigne la source de la session et `{output}` la destination intermédiaire à créer. Un code de sortie non nul, un dépassement du délai ou une sortie invalide fait échouer la conversion.

Exemple fourni : `scripts/converters/solidworks.example.json`. Adapter son chemin absolu au dossier réellement utilisé :

```json
{
  "schema": "atelier.cad_converters.v1",
  "converters": [
    {
      "name": "SOLIDWORKS Windows COM vers STEP",
      "extensions": [".sldprt", ".sldasm"],
      "command": [
        "powershell.exe", "-NoProfile", "-STA", "-File",
        "C:/Mailleur/atelier_mailleur/scripts/converters/solidworks_to_step.ps1",
        "-InputPath", "{input}", "-OutputPath", "{output}"
      ],
      "output_extension": ".step",
      "timeout": 120
    }
  ]
}
```

Dans le terminal PowerShell utilisé pour lancer Mailleur :

```powershell
$env:MAILLEUR_CAD_CONVERTERS = 'C:/Mailleur/atelier_mailleur/scripts/converters/solidworks.example.json'
```

Cette configuration ne télécharge ni SOLIDWORKS ni une licence. Elle ne doit contenir que des commandes locales dont l'utilisateur connaît la fonction. Ne pas ajouter une extension au convertisseur SOLIDWORKS pour prétendre lui faire lire un autre format natif : le script refuse les extensions hors `.sldprt` et `.sldasm`.

## Exécuter la passerelle SOLIDWORKS seule

Interface exacte du script :

```powershell
powershell.exe -NoProfile -STA -File .\scripts\converters\solidworks_to_step.ps1 `
  -InputPath 'C:\Pieces\piece.sldprt' -OutputPath 'C:\Echanges\piece.step'
```

Options supplémentaires :

- `-Configuration 'NomExact'` : demande une configuration précise. À défaut, SOLIDWORKS choisit celle ouverte selon le document ; le rapport donne son nom.
- `-AllowSuppressedComponents` : autorise explicitement l'exclusion des composants supprimés dans cette configuration. Sans ce paramètre, leur présence provoque un refus. Elle ne réactive aucun composant et ne permet pas d'ignorer des références manquantes ou des composants non résolus.

Le script écrit un rapport JSON compact sur la sortie standard en cas de succès, avec la révision de SOLIDWORKS, la configuration, les codes d'ouverture/export et le compte d'exclusions. Il retourne `0` uniquement après création du STEP. En cas d'échec, un diagnostic JSON est envoyé sur la sortie d'erreur et le code vaut `1`. Il refuse de remplacer un fichier de destination existant.

Utiliser PowerShell 64 bits en mode STA, dans une session Windows de bureau disposant de SOLIDWORKS desktop et de son API COM. La disponibilité de la classe COM `SldWorks.Application`, les droits de lancement et les fonctions d'export dépendent de l'installation. Respecter sa politique d'exécution PowerShell ; la passerelle ne la désactive pas. Le script n'est pas présenté comme validé pour un service Windows sans session interactive, SOLIDWORKS Connected, une licence Maker ou un poste distant automatisé.

### Traitement effectivement demandé à SOLIDWORKS

1. Création de l'objet `SldWorks.Application`, sans attachement explicite à un document utilisateur. Si la session obtenue contient déjà des documents ou si la source est déjà ouverte, l'opération est refusée. Ne pas utiliser la même session SOLIDWORKS en parallèle pendant la conversion.
2. Ouverture en lecture seule par `OpenDoc6` avec les options Silent, ReadOnly et OverrideDefaultLoadLightweight, sans demander le chargement léger. Toute erreur ou tout avertissement d'ouverture est refusé ; le chemin retourné doit correspondre à la source. La disponibilité et la sémantique de ces options sont décrites par [OpenDoc6](https://help.solidworks.com/2025/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.ISldWorks~OpenDoc6.html) et [swOpenDocOptions](https://help.solidworks.com/2022/english/api/swconst/SolidWorks.Interop.swconst~SolidWorks.Interop.swconst.swOpenDocOptions_e.html?id=2.10.1.540).
3. Activation du document sans reconstruction automatique. Une reconstruction nécessaire doit être réalisée et enregistrée dans le logiciel d'origine avant un nouvel essai. [ActivateDoc3](https://help.solidworks.com/2014/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.ISldWorks~ActivateDoc3.html), [options de reconstruction](https://help.solidworks.com/2021/english/api/swconst/SolidWorks.Interop.swconst~SolidWorks.Interop.swconst.swRebuildOnActivation_e.html?id=2.10.1.612).
4. Pour un assemblage, résolution des composants légers puis contrôle des composants de la configuration active. Les configurations/composants SpeedPak, les références non chargées, les états incohérents et les composants restant légers sont refusés. Les composants supprimés sont refusés sauf exclusion explicitement autorisée. Il n'y a aucune activation automatique de composants ou substitution de fichiers. [Résolution des composants](https://help.solidworks.com/2022/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IAssemblyDoc~ResolveAllLightWeightComponents.html), [états des composants](https://help.solidworks.com/2025/english/api/swconst/SolidWorks.Interop.swconst~SolidWorks.Interop.swconst.swComponentSuppressionState_e.html?id=2.10.1.166), [GetComponents](https://help.solidworks.com/2024/english/api/sldworksapi/solidworks.interop.sldworks~solidworks.interop.sldworks.iassemblydoc~getcomponents.html), [SpeedPak](https://help.solidworks.com/2025/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IComponent2~IsSpeedPak.html).
5. Effacement de la sélection avec `ClearSelection2`, puis export par `IModelDocExtension.SaveAs` vers un fichier STEP temporaire. Cette API stable antérieure à 2020 reste documentée mais est marquée obsolète au profit de méthodes plus récentes ; elle a été choisie pour conserver un contrat ancien et simple. Les faces sélectionnées pourraient sinon restreindre l'export. Le script exige un retour vrai, aucun code d'erreur et aucun avertissement d'export, puis contrôle l'existence et la signature du fichier avant de le publier. [SaveAs et conditions d'export STEP](https://help.solidworks.com/2019/english/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IModelDocExtension~SaveAs.html), [documentation récente de SaveAs](https://help.solidworks.com/2026/English/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IModelDocExtension~SaveAs.html).

Les API utilisées sont documentées avant ou dans la génération 2020 ; leur disponibilité documentaire ne remplace pas un essai avec chaque version installée et chaque type de fichier.

Avant l'ouverture, le script positionne le répertoire courant SOLIDWORKS sur celui de la source et restaure sa valeur initiale lors du nettoyage. Les dossiers de recherche personnalisés restent prioritaires selon SOLIDWORKS ; vérifier que le logiciel trouve les bonnes références, en particulier si plusieurs versions portent le même nom. [Répertoire de recherche utilisé par OpenDoc6](https://help.solidworks.com/2019/english/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.ISldWorks~SetCurrentWorkingDirectory.html).

Le script n'appelle ni sauvegarde native de la source, ni fermeture de tous les documents, ni arrêt de SOLIDWORKS. Il ferme seulement le document qu'il a ouvert après contrôle de son chemin. Une session SOLIDWORKS peut rester démarrée. Si le délai du processus hôte expire brutalement ou si SOLIDWORKS ouvre une boîte de dialogue bloquante, son nettoyage normal peut ne pas s'exécuter ; examiner alors la session avant de relancer. Le mutex du script empêche deux conversions de cette passerelle de se lancer simultanément, sans verrouiller l'interface contre un utilisateur.

Les préférences d'export STEP déjà configurées dans SOLIDWORKS ne sont pas modifiées. Il faut contrôler le STEP obtenu, ses unités, ses solides, ses instances et son orientation dans Mailleur, puis la qualité du maillage. L'arbre de construction paramétrique, les contraintes d'assemblage, les lois de contact et les données d'un calcul mécanique ne sont pas importés par cette traduction géométrique.

## Références d'assemblage et `.cadpack`

Un fichier `.sldasm` peut référencer des pièces dans d'autres fichiers. Transmettre uniquement ce fichier au serveur ou le copier dans une session isolée ne transporte pas les pièces référencées. La même difficulté existe pour certaines pièces dérivées et leurs références externes. Un STEP complet et autonome exporté en amont reste le parcours le plus simple à partager.

Le conteneur Mailleur `.cadpack` est un ZIP de transport contenant `cadpack.json`, la source et ses dépendances dans une arborescence relative. Exemple de manifeste :

```json
{"schema":"atelier.cadpack.v1","source":"assembly.sldasm"}
```

Exemple d'arborescence :

```text
assembly.cadpack
├── cadpack.json
├── assembly.sldasm
└── parts/
    ├── body.sldprt
    └── support.sldprt
```

Préserver les références à ces fichiers ; ajouter simplement des pièces portant le bon nom dans le ZIP ne répare pas des liens absolus ou des configurations absentes. La fonction officielle [Pack and Go de SOLIDWORKS](https://help.solidworks.com/2020/English/SolidWorks/sldworks/c_pack_and_go.htm?id=adc0969bc83a4984a4fb361972d7ef18) permet de rassembler les fichiers liés. Préparer un dossier cohérent, vérifier qu'il s'ouvre correctement dans SOLIDWORKS, puis ajouter le manifeste et créer le conteneur Mailleur. Un ZIP Pack and Go sans ce manifeste ne devient pas automatiquement un `.cadpack` valide.

La validation du conteneur Mailleur porte sur les chemins relatifs, les limites et les fichiers présents. Elle ne prouve pas que toutes les références internes de l'éditeur sont résolues : le convertisseur doit encore refuser les avertissements et vérifier le document ouvert. Le `.cadpack` ne contient pas le logiciel CAO ni sa licence.

## Validation à réaliser sous Windows

Le fichier `scripts/converters/test_solidworks_bridge.ps1` fournit neuf scénarios de protocole avec doubles PowerShell : succès, session occupée, exclusion refusée/autorisée, avertissement d'ouverture, erreur de sauvegarde, propriété du document, signature de sortie et refus d'écrasement. Ces neuf scénarios ont été exécutés avec succès le 7 novembre 2026 sous **PowerShell 7.6.6 sur Mac arm64**, avec une distribution portable officielle temporaire. Ils ne lancent pas SOLIDWORKS et ne valident ni COM Windows, ni une conversion industrielle réelle. Le journal est conservé dans `verification/rc6_20261107/solidworks_mock_tests.txt`.

```powershell
powershell.exe -NoProfile -File .\scripts\converters\test_solidworks_bridge.ps1
```

Avant d'annoncer un support SOLIDWORKS opérationnel, compléter ces tests simulés par un essai réel sur au moins une pièce, un assemblage à références externes et un assemblage à composants supprimés. Vérifier l'absence de modification de la source et d'effet sur les documents préexistants, le nombre de corps/solides, les unités et la géométrie importée. Faire ces essais pour les versions de SOLIDWORKS effectivement visées ; la réussite des doubles et la lecture de la documentation ne suffisent pas à les déclarer réussis.

## Licence et distribution

Le projet distribue un script d'automatisation, sans binaire, SDK ni licence SOLIDWORKS. Le poste qui le lance doit disposer d'une installation et de droits permettant cet usage et l'export des documents concernés. Les droits sur les géométries et leurs dépendances restent ceux de leurs auteurs. Redistribuer Mailleur n'autorise pas à redistribuer les logiciels propriétaires nécessaires aux convertisseurs. Les règles et droits d'un traducteur externe différent doivent être documentés séparément.
