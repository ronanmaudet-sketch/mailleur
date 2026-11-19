# Pilote d’apprentissage sur quadrilatères et hexaèdres directs

Le moteur mailleur-core 1.0.0-rc.10 a exécuté 32 configurations sur Darwin arm64. Le lot comprend 16 maillages quadrilatéraux et 16 maillages hexaédriques. Le noyau a accepté 96 des 96 stratégies essayées.

Le réseau a été entraîné sur 20 configurations. 6 autres ont servi à décider de son activation, et 6 à l’évaluation finale. Le classement est activé selon la règle de ce pilote. Sa portée est limitée aux familles géométriques testées.

- [Rapport, empreinte du moteur et résultats par partition](summary.json)
- [Six familles de géométrie et répartition](runs/20261119T195521802910/manifest.json)
- [Géométries, réglages et résultats de chaque essai](runs/20261119T195521802910/shards/)
- [Modèle entraîné, 124 972 octets](runs/20261119T195521802910/models/current.json)
- [Point de reprise de l’entraînement](runs/20261119T195521802910/models/training.checkpoint.json)
- [Plan d’un million de configurations, avec zéro cas calculé](target_plan/manifest.json)
