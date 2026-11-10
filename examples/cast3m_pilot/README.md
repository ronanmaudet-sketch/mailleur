# Exemple pilote Mailleur → Cast3M

**Auteur : Projet Mailleur.** Exemple public d’une traction élastique sur un bloc
de 2 × 1 × 1 mm, avec un maillage tétraédrique quadratique. Il permet de relire
un UNV dans Cast3M et de vérifier le calcul par une solution analytique.

## Exécuter le parcours complet

Installer Mailleur selon le README principal, activer son environnement Python,
puis, depuis la racine du dépôt :

```sh
python examples/cast3m_pilot/run_example.py \
  --castem /chemin/vers/castem26 \
  --output output/cast3m_pilot
```

Cast3M doit être installé séparément. `--castem` accepte son lanceur, par chemin
ou par nom dans le `PATH`. Choisir un dossier de sortie neuf ou vide. La commande
renvoie zéro seulement si la génération, l’exécution Cast3M et les comparaisons
réussissent. Elle utilise le Python courant et ses dépendances Mailleur.

Pour cet exemple utilisé comme dossier autonome, la commande équivalente est :

```sh
python run_example.py --castem /chemin/vers/castem26 --output resultats
```

## Tester le maillage fourni sans installer Mailleur

Le dossier `reference/` contient les données du calcul réellement exécuté.
Avec un Python disposant de sa bibliothèque standard et Cast3M installé :

```sh
python examples/cast3m_pilot/run_example.py --reference \
  --castem /chemin/vers/castem26 \
  --output output/cast3m_reference
```

Cette option copie les entrées dans le nouveau dossier de sortie et réexécute
Cast3M. Ce chemin a aussi été testé avec Python 3.9.6, dans lequel Mailleur
n’était pas installé. La génération complète utilise le Python 3.11–3.13
requis par Mailleur rc7.

## Fichiers et fonctionnement

1. `project.json` définit le bloc et la taille de maillage de 0,4 mm, à l’ordre 2.
   `generer_maillage.py` appelle directement l’API Python locale `NativeSession`.
   La génération et la conversion des coordonnées s’exécutent dans le noyau
   Rust, avec Gmsh/OpenCASCADE ; ce parcours n’utilise pas de serveur HTTP.
2. `NativeSession.export(..., "cast3m", units="mm")` produit
   `import_cast3m.unv`, le pont `import_cast3m.dgibi` et un JSON déclarant les
   unités. Le maillage interne est en mètres et l’export demandé est en mm.
3. Le générateur assemble `traction.dgibi` : dimension 3, pont d’import exporté,
   puis `mecanique.dgibi`. Ce dernier fichier définit explicitement modèle,
   matériau, appuis et chargement. Ces choix appartiennent à cet exemple.
4. `verifier_calcul.py` lance Cast3M dans un dossier temporaire, vérifie les
   sommes de contrôle des entrées et compare les résultats. Il conserve
   `resultat_cast3m.json` et `cast3m_extrait.log` dans le dossier de sortie.

Le pont d’import contient les instructions suivantes :

```text
TAB1 = LIRE 'UNV' 'import_cast3m.unv' ;
MAIL1 = TAB1 . 'MAILLAGE' ;
```

Le calcul sélectionne `TAB1.'DOMAIN'` et `TAB1.'BOUNDARY'`. Les onze groupes
d’éléments de cet exemple et leurs groupes nodaux `_GN` sont comptés dans
Cast3M et comparés au maillage source. Les 443 éléments lus dans la référence
comprennent 221 TE10 de volume, 178 TRI6 de frontière et 44 SEG3 d’arête.

Pour lancer directement le jeu de données généré, depuis son dossier :

```sh
castem26 -MEM 512Mo -NCPU 1 -NOSWAP traction.dgibi
```

Ce lancement direct affiche le journal Cast3M. `run_example.py` ajoute les
comparaisons automatiques et conserve les fichiers d’entrée inchangés.

## Solution mécanique et résultats attendus

Les coordonnées sont interprétées en **mm**, les forces en **N**, et les
contraintes et le module d’Young en **N/mm²** :

- E = 210 000 N/mm² ; coefficient de Poisson ν = 0,3.
- Traction uniforme de 10 N/mm² sur la face x = 2 mm ; autres faces libres.
- UX = 0 sur x = 0 ; UY = 0 sur y = 0 ; UZ = 0 sur z = 0.
  Ces appuis permettent la contraction de Poisson.

La solution est `ux = 10 x / E`, `uy = −0,3 × 10 y / E` et
`uz = −0,3 × 10 z / E`. On attend donc :

| Grandeur | Référence analytique |
|---|---:|
| Déplacement axial maximal | 9,5238095238 × 10⁻⁵ mm |
| Contraction transversale maximale en valeur absolue | 1,4285714286 × 10⁻⁵ mm |
| Contrainte axiale uniforme | 10 N/mm² |
| Effort appliqué et réaction axiale | +10 N et −10 N |

Les 43 comparaisons portent sur les effectifs, groupes, bornes géométriques,
déplacements, contraintes et équilibre des forces. L’erreur des champs de
déplacement est calculée dans Cast3M sur tous les nœuds ; la limite retenue est
10⁻⁸ du déplacement axial de référence. Les valeurs affichées par `MESS` sont
comparées avec une tolérance relative de 2 × 10⁻⁶, adaptée à leurs six chiffres
significatifs. Ces 43 comparaisons concernent **un seul cas**.

## Exécution de référence du 10 novembre 2026

Le parcours complet a été exécuté avec Mailleur **1.0.0rc7**, Gmsh **4.15.2**,
Python **3.12.16** et Cast3M **2026.0**, sur macOS **27.2 arm64**. Le vrai binaire
Cast3M est un exécutable arm64 natif. Le résultat est **43/43 comparaisons
réussies**, code de retour 0 et niveau d’erreur Cast3M 0.

La référence comporte **497 nœuds**. Ces effectifs sont un relevé de cette
exécution, pas des constantes imposées à d’autres versions du mailleur : une
nouvelle génération compare les comptes effectivement obtenus. Le lancement,
l’import et la résolution ont pris environ **0,81 s** ; la génération et
l’export sont mesurés séparément dans `reference/maillage.json`. Ces durées
ponctuelles ne prédisent pas celles d’un modèle industriel.

Cast3M a également signalé `IEEE_INVALID_FLAG`. Ce drapeau est conservé dans
le résultat et dans l’extrait du journal, avec les 43 comparaisons et le niveau
d’erreur 0. Les en-têtes du lanceur contenant des noms d’utilisateur ou chemins
locaux sont exclus de l’extrait public ; il ne s’agit pas du journal complet.

`reference/resultat_cast3m.json` contient chaque valeur et sa tolérance,
les versions et les empreintes du lanceur/binaire utilisés.
`reference/reexecution_sans_mailleur.json` consigne le second parcours, à partir
du seul maillage fourni. `SHA256SUMS` permet de vérifier les fichiers livrés :

```sh
# Depuis le dossier de cet exemple, sous macOS :
shasum -a 256 -c SHA256SUMS
# Sous Linux : sha256sum -c SHA256SUMS
```

## Origine et périmètre

Ces scripts adaptent le seul cas `volume_p2_mm` du validateur
`scripts/validate_cast3m.py`, livré avec Mailleur rc7. Le jeu de données est
synthétique. Les scripts et données de cet exemple sont distribués sous
**GPL-3.0-or-later**, avec le texte de licence dans `LICENSE`. Le lanceur et le
binaire Cast3M restent ceux de l’installation de l’utilisateur.

Le calcul démontre ce chemin UNV/Gibiane, ces groupes et ces éléments
quadratiques en élasticité linéaire. Il ne qualifie pas les coques,
hexaèdres, contacts, non-linéarités ou toutes les versions de Cast3M. Pour
changer la géométrie, les unités ou la physique, adapter aussi
`mecanique.dgibi` et les références de `verifier_calcul.py`.

Notices primaires : [LIRE](https://www-cast3m.cea.fr/index.php?notice=LIRE&page=notices_classees),
[MODE](https://www-cast3m.cea.fr/index.php?notice=MODE&page=notices_classees),
[FSUR](https://www-cast3m.cea.fr/index.php?notice=FSUR&page=notices_classees),
[RESO](https://www-cast3m.cea.fr/index.php?notice=RESO&page=notices_classees) et
[SIGM](https://www-cast3m.cea.fr/index.php?notice=SIGM&page=notices_classees).
