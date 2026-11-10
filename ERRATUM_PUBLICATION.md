# Erratum de publication — 10 novembre 2026

Les archives rc7 du 7 novembre et les PDF/Word historiques sont conservés sans
réécriture ; le présent erratum précise leurs deux limites de présentation.
Les essais supplémentaires réalisés pour la publication sont décrits dans
[leur rapport dédié](verification/publication_20261110/README.md), et le
[correctif Windows](docs/provenance/CORRECTIF_WINDOWS.md) possède sa propre provenance.

## Durées Cast3M finales

La plage **0,47 à 0,65 s** restée dans `docs/VALIDATION_RC7.md` et dans des
exports de guide correspondait à un rejeu antérieur. La campagne finale
avec le noyau `39fde2270d7282ebe460591308edebd6d7a978601e519fdddf441ee8d9d9cd85`
mesure **0,756 à 1,016 s** sur les cinq cas. Les durées arrondies pour lecture sont
1,016007 ; 0,870752875 ; 0,756339834 ; 0,870184375 ; 0,818772791 secondes.

Ces temps couvrent lancement de Cast3M, lecture UNV, contrôles et résolution,
à l’exclusion de la génération et de l’export du maillage. Ils restent des
mesures ponctuelles de petites éprouvettes, pas un dimensionnement industriel.
Les **5 cas et 193 contrôles réussis** ne changent pas. Les [sources finales](docs/cast3m/preuves/README.md)
et le [résumé](docs/cast3m/preuves/summary.json) étayent cette rectification.
Le Markdown de validation public est corrigé ; les anciens PDF/Word ne sont
pas régénérés afin de conserver leur identité historique.

## Portée du dépôt léger

Les données brutes et anciennes sources rc4 ne sont pas incluses dans ce
dépôt. Les guides historiques qui les annoncent comme disponibles doivent
être lus avec la [notice des preuves externes](docs/provenance/PREUVES_EXTERNES.md).
Les copies publiques de résumés et journaux sont expurgées de chemins et du
nom d’utilisateur ; les valeurs de calcul sont conservées, avec leurs
[empreintes d’origine et de publication](docs/provenance/README.md).
