# API de scripts et couplage solveur — version 1.0

Le noyau Rust peut être utilisé sans ouvrir l'interface. Le client Python
transporte les projets et les fichiers ; le calcul de maillage, la qualité et
la transformation des indicateurs en raffinements restent en Rust/natif.

Le couplage est **neutre vis-à-vis du solveur**. Il peut être intégré à un
wrapper pour Code_Aster, CalculiX, Abaqus, ANSYS, Cast3M ou un autre logiciel
capable d'importer un format fourni et de produire les indicateurs décrits
ci-dessous. Cela ne signifie pas que des adaptateurs complets pour ces
logiciels sont livrés ou que leurs calculs ont été exécutés ici.

## Mailler et exporter depuis Python

Installer le projet, puis exécuter le script depuis son environnement :

```python
from atelier.api import MeshingClient

project = {
    "schema_version": 1,
    "name": "Bloc quadratique",
    "mode": "volume",
    "units": {"input": "mm", "internal": "m"},
    "geometry": {"kind": "box", "width": 4, "height": 3, "depth": 2},
    "mesh": {
        "element_shape": "hexahedron",
        "order": 2,
        "quadratic_layout": "serendipity",
        "structured": {"divisions": [4, 3, 2]},
        "size": 1,
        "min_size": 0.1,
        "recombine": False,
        "max_elements": 10000,
    },
}

with MeshingClient("output/script", timeout=120) as client:
    print(client.capabilities())
    mesh = client.generate(project)
    print(mesh.mesh_file, mesh.mesh_id)
    export = client.export(mesh, "output/script/resultat", format_name="msh", units="mm")
    print(export["file"])
```

Le choix de forme est strict : une géométrie incompatible provoque une erreur,
sans remplacement silencieux par des triangles ou des tétraèdres. L'ordre et
le format d'export doivent être compatibles. Les coordonnées internes du MSH
natif sont en mètres ; `export` produit les coordonnées dans l'unité demandée.

Exemple complet : [api_generate.py](../examples/api_generate.py) et
[structured_hexa_api.json](../examples/structured_hexa_api.json).

## Méthodes publiques

```python
from atelier.api import MeshingClient, MeshResult, load_result
```

| Méthode | Rôle |
| --- | --- |
| `MeshingClient(workspace, timeout=300)` | Session synchrone et dossier de travail distinct |
| `capabilities()` | Capacités JSON déclarées par le binaire Rust installé |
| `inspect(project, source=None)` | Examen géométrique ; résultat d'affichage uniquement |
| `generate(project, source=None, use_advisor=False)` | Génération d'un maillage contrôlé |
| `learn(project, source=None, **search)` | Recherche et mémorisation de recettes ; paramètres de recherche du noyau |
| `remesh(project, previous=mesh, source=None)` | Reprise locale explicite ; refus si le noyau ne peut pas préserver l'extérieur |
| `export(mesh, output, format_name="msh", units=None)` | Export du résultat accepté ; unité d'entrée du projet par défaut |
| `plan_adaptation(mesh, indicators, tolerance=.05, max_regions=256)` | Analyse des indicateurs dans Rust, sans lancer le solveur ni remailler |
| `adapt_loop(project, solver, solver_name=..., physics=..., ...)` | Boucle export–solveur–plan d'adaptation–nouvelle génération |
| `cancel()` / `reset_cancel()` | Annulation puis réarmement explicite de la session |
| `load_result(directory)` | Réouverture d'un dossier natif persistant, sans recalcul |

Les arguments optionnels après `project` sont nommés. Les méthodes de génération
renvoient un `MeshResult` avec `directory`, `project`, `report`, `mesh_file`,
`mesh_id` et `to_dict()`. `mesh_id` est le SHA-256 du contenu de `mesh.msh`.
Le résultat d'une recherche contient les réglages effectivement retenus.

Une instance sérialise ses appels. Pour des calculs indépendants parallèles,
utiliser plusieurs instances et des dossiers de travail distincts. Chaque
calcul natif est isolé dans un processus. Le client n'entretient pas encore
un noyau CAO persistant ; les temps d'import/export restent à inclure dans
l'évaluation d'un couplage fréquent.

## Contrat d'échange avec le solveur

L'adaptateur reçoit un `SolverIteration` contenant :

- `index` : indice d'évaluation, commençant à zéro ;
- `mesh` et `mesh_id` : résultat courant et son empreinte ;
- `export` : fichier produit et unité de sortie ;
- `exchange_directory` : dossier de cette évaluation ;
- `solver`, `physics`, `units` : contexte explicite.

Il doit exécuter ou attendre **le logiciel utilisé pour le calcul final**, puis
retourner un objet JSON de ce type :

```json
{
  "schema": "atelier.solver.indicators.v1",
  "solver": "mon-solveur",
  "physics": "static",
  "units": "mm",
  "mesh_id": "EMPREINTE_SHA256_DU_MSH_INTERNE_COURANT_SUR_64_CARACTERES",
  "converged": false,
  "regions": [
    {
      "center": [2, 1.5, 1],
      "radius": 1,
      "error": 0.2,
      "target_size": 0.5
    }
  ]
}
```

Le placeholder d'empreinte doit être remplacé par `iteration.mesh_id`, ou par
la valeur du fichier de demande. Il identifie le maillage interne à partir
duquel l'export a été créé, et non les octets du fichier converti.

`center`, `radius` et `target_size` sont exprimés dans `units`. Le callback de
la boucle utilise l'unité de l'export demandé. L'appel direct `adapt_plan`
peut recevoir une autre unité prise en charge : Rust effectue la conversion.
`error` est un indicateur fini non négatif, dans la même convention que
`tolerance`. Par exemple, 0,05 signifie 5 % uniquement si le wrapper fournit
un indicateur d'erreur relative. Le mailleur n'invente pas cette définition.

`target_size` est optionnel. En son absence, Rust déduit une réduction de taille
bornée. Les régions concernées sont celles dont `error > tolerance`. La taille
minimale et le budget d'éléments continuent de s'appliquer. La convergence doit
être déclarée explicitement par le solveur ; une absence de régions à raffiner
n'est pas présentée comme une preuve de convergence. Un retour annonçant la
convergence tout en conservant des régions au-dessus du seuil est refusé.

Les empreintes périmées, le mauvais solveur ou la mauvaise physique, une unité
incohérente et les valeurs JSON non finies sont refusés. Le fichier de maillage
original doit rester inchangé pendant le calcul ; le solveur peut en utiliser
une copie dans son propre dossier.

## Boucle pilotée par callback ou fichiers

```python
from atelier.api import MeshingClient, JsonFileSolverAdapter
from mon_adaptateur import run

# run(request_path, feedback_path) est fourni par l'utilisateur.
# Il traduit la demande vers son solveur et écrit les indicateurs JSON.
with MeshingClient("output/couplage", timeout=300) as client:
    result = client.adapt_loop(
        project,
        JsonFileSolverAdapter(run),
        solver_name="mon-solveur",
        physics="static",
        format_name="msh",
        units="mm",
        tolerance=0.05,
        max_regions=256,
        max_iterations=5,
        max_seconds=3600,
    )
    print(result["converged"], result["stop_reason"])
```

`JsonFileSolverAdapter` écrit `solver_request.json`, appelle le wrapper et lit
`solver_feedback.json` (limite de 8 Mio). Sans wrapper, le fichier de réponse
doit déjà exister. Il n'exécute aucune commande contenue dans le JSON. Un
callback direct peut également retourner le dictionnaire sans passer par un
fichier. Les journaux conservent les demandes, retours et plans de chaque
itération, puis `adaptation_run.json`.

La boucle s'arrête sur convergence déclarée et cohérente, absence de changement
réalisable, absence de progression, nombre maximal d'évaluations, budget de
temps, annulation ou erreur. `max_iterations` borne les **évaluations du
solveur** : aucun dernier maillage inutile n'est généré après épuisement du
budget. Les processus natifs ont un délai maximal réduit au budget restant.
Le callback solveur doit gérer son propre délai et son annulation ; Python ne
peut pas interrompre arbitrairement une fonction fournie par l'appelant.

Un dépassement de budget ou une annulation lève une exception et laisse un
rapport indiquant l'arrêt. Les refus de projet ou de protocole lèvent notamment
`ValueError`, les refus natifs `RuntimeError`, et un budget écoulé vérifié par
la boucle `TimeoutError`.

La stratégie native apparaît dans le plan : les maillages structurés sont
régénérés globalement avec une discrétisation structurée conforme. Cela peut
raffiner plus largement que les régions indiquées. Les domaines non structurés
utilisent des champs de taille isotropes. La boucle ne prétend pas conserver
localement des cellules si le noyau effectue une nouvelle génération globale.

Exemple exécutable : [api_solver_loop.py](../examples/api_solver_loop.py).
Son option `--demo` fournit des indicateurs **fictifs** pour vérifier les échanges ;
elle ne résout aucune équation et ne démontre aucune convergence physique.

## Reprise physique et validation

Avant de poursuivre un calcul transitoire ou non linéaire sur le nouveau
maillage, le solveur doit gérer les déplacements, vitesses, températures,
contraintes, variables internes, contacts et autres états nécessaires à sa
formulation. La conservation et l'interpolation de ces champs ne sont pas
effectuées implicitement par le mailleur. Une simple conversion de fichier
ne suffit pas à garantir une reprise correcte.

Le même contrat d'échange sert pour plusieurs logiciels. Chaque adaptateur
réel doit néanmoins être validé dans son solveur : formulation d'élément,
groupes, conditions limites, reprise des états et critères de convergence.
Les tests de livraison distinguent les contrôles de protocole avec fixtures,
la génération native et les calculs physiques réellement exécutés.

## Interface JSON en ligne de commande

```sh
python -m atelier capabilities --workspace output/api

python -m atelier adapt-plan --previous output/api/native/IDENTIFIANT \
  --indicators retour_solveur.json --workspace output/api \
  --tolerance 0.05 -o plan_adaptation.json
```

Le plan contient `selected_project` ; le script peut ensuite appeler la commande
`mesh` ou `MeshingClient.generate` avec ce projet. L'opération native correspondante
est `adapt_plan`, par le même protocole `mailleur-core --request demande.json`.
`capabilities` et `adapt_plan` ne nécessitent pas de charger Gmsh ; la génération
et les exports le nécessitent.
