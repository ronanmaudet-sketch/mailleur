# Inventaire technique des dépendances Rust et notices copiées

Relevé du **7 novembre 2026**, pour le noyau `mailleur-core` rc7 compilé sur
**Apple M3 / aarch64-apple-darwin**, avec Rust 1.99.1. Les versions et sommes de
paquets proviennent de `rust_core/Cargo.lock` ; les déclarations de licence et
les notices proviennent exclusivement du cache Cargo local existant. Aucun
paquet ni texte de licence n'a été téléchargé pour cet inventaire.

Actualisation rc7 : mêmes dépendances verrouillées ; `serde_json` active la
fonction `float_roundtrip`, sans nouveau paquet. L'empreinte du Cargo.lock a
été actualisée dans l'inventaire après vérification des notices copiées.

## Périmètre

Les 22 paquets disponibles localement appartiennent au graphe de compilation
utilisé sur le Mac. Les macros et scripts de compilation sont distingués des
bibliothèques de la cible. Cette liste ne prétend pas identifier chaque symbole
conservé dans l'exécutable après l'optimisation et l'édition de liens LTO.
Les notices de compilation sont aussi conservées pour permettre la reconstruction.

Les expressions de licence ci-dessous sont **les valeurs déclarées par chaque
paquet**, recopiées sans choisir une autre licence en son nom. Le choix de la
licence du code propre à Mailleur figure dans le `LICENSE` de la racine ; il ne
remplace pas les notices des composants tiers. Les fichiers de notice ci-joints
sont copiés intégralement, avec leurs attributions originales.

| Paquet | Version verrouillée | Licence déclarée dans Cargo.toml | Rôle dans cette construction | Notices locales copiées |
| --- | --- | --- | --- | --- |
| `block-buffer` | 0.10.4 | `MIT OR Apache-2.0` | Bibliothèque du graphe cible M3 | [LICENSE-APACHE](licenses/rust/block-buffer-0.10.4/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/block-buffer-0.10.4/LICENSE-MIT) |
| `cfg-if` | 1.0.5 | `MIT OR Apache-2.0` | Bibliothèque du graphe cible M3 | [LICENSE-APACHE](licenses/rust/cfg-if-1.0.5/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/cfg-if-1.0.5/LICENSE-MIT) |
| `cpufeatures` | 0.2.17 | `MIT OR Apache-2.0` | Bibliothèque du graphe cible M3 | [LICENSE-APACHE](licenses/rust/cpufeatures-0.2.17/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/cpufeatures-0.2.17/LICENSE-MIT) |
| `crypto-common` | 0.1.7 | `MIT OR Apache-2.0` | Bibliothèque du graphe cible M3 | [LICENSE-APACHE](licenses/rust/crypto-common-0.1.7/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/crypto-common-0.1.7/LICENSE-MIT) |
| `digest` | 0.10.7 | `MIT OR Apache-2.0` | Bibliothèque du graphe cible M3 | [LICENSE-APACHE](licenses/rust/digest-0.10.7/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/digest-0.10.7/LICENSE-MIT) |
| `generic-array` | 0.14.7 | `MIT` | Bibliothèque du graphe cible M3 | [LICENSE](licenses/rust/generic-array-0.14.7/LICENSE) |
| `itoa` | 1.0.18 | `MIT OR Apache-2.0` | Bibliothèque du graphe cible M3 | [LICENSE-APACHE](licenses/rust/itoa-1.0.18/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/itoa-1.0.18/LICENSE-MIT) |
| `libc` | 0.2.191 | `MIT OR Apache-2.0` | Bibliothèque du graphe cible M3 | [LICENSE-APACHE](licenses/rust/libc-0.2.191/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/libc-0.2.191/LICENSE-MIT) |
| `libloading` | 0.8.9 | `ISC` | Bibliothèque du graphe cible M3 | [LICENSE](licenses/rust/libloading-0.8.9/LICENSE) |
| `memchr` | 2.8.3 | `Unlicense OR MIT` | Bibliothèque du graphe cible M3 | [COPYING](licenses/rust/memchr-2.8.3/COPYING), [LICENSE-MIT](licenses/rust/memchr-2.8.3/LICENSE-MIT), [UNLICENSE](licenses/rust/memchr-2.8.3/UNLICENSE) |
| `proc-macro2` | 1.0.107 | `MIT OR Apache-2.0` | Compilation : macros ou script de build | [LICENSE-APACHE](licenses/rust/proc-macro2-1.0.107/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/proc-macro2-1.0.107/LICENSE-MIT) |
| `quote` | 1.0.47 | `MIT OR Apache-2.0` | Compilation : macros ou script de build | [LICENSE-APACHE](licenses/rust/quote-1.0.47/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/quote-1.0.47/LICENSE-MIT) |
| `serde` | 1.0.229 | `MIT OR Apache-2.0` | Bibliothèque du graphe cible M3 | [LICENSE-APACHE](licenses/rust/serde-1.0.229/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/serde-1.0.229/LICENSE-MIT) |
| `serde_core` | 1.0.229 | `MIT OR Apache-2.0` | Bibliothèque du graphe cible M3 | [LICENSE-APACHE](licenses/rust/serde_core-1.0.229/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/serde_core-1.0.229/LICENSE-MIT) |
| `serde_derive` | 1.0.229 | `MIT OR Apache-2.0` | Compilation : macros ou script de build | [LICENSE-APACHE](licenses/rust/serde_derive-1.0.229/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/serde_derive-1.0.229/LICENSE-MIT) |
| `serde_json` | 1.0.154 | `MIT OR Apache-2.0` | Bibliothèque du graphe cible M3 | [LICENSE-APACHE](licenses/rust/serde_json-1.0.154/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/serde_json-1.0.154/LICENSE-MIT) |
| `sha2` | 0.10.9 | `MIT OR Apache-2.0` | Bibliothèque du graphe cible M3 | [LICENSE-APACHE](licenses/rust/sha2-0.10.9/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/sha2-0.10.9/LICENSE-MIT) |
| `syn` | 3.0.9 | `MIT OR Apache-2.0` | Compilation : macros ou script de build | [LICENSE-APACHE](licenses/rust/syn-3.0.9/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/syn-3.0.9/LICENSE-MIT) |
| `typenum` | 1.20.1 | `MIT OR Apache-2.0` | Bibliothèque du graphe cible M3 | [LICENSE](licenses/rust/typenum-1.20.1/LICENSE), [LICENSE-APACHE](licenses/rust/typenum-1.20.1/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/typenum-1.20.1/LICENSE-MIT) |
| `unicode-ident` | 1.0.27 | `(MIT OR Apache-2.0) AND Unicode-3.0` | Compilation : macros ou script de build | [LICENSE-APACHE](licenses/rust/unicode-ident-1.0.27/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/unicode-ident-1.0.27/LICENSE-MIT), [LICENSE-UNICODE](licenses/rust/unicode-ident-1.0.27/LICENSE-UNICODE) |
| `version_check` | 0.9.5 | `MIT/Apache-2.0` | Compilation : macros ou script de build | [LICENSE-APACHE](licenses/rust/version_check-0.9.5/LICENSE-APACHE), [LICENSE-MIT](licenses/rust/version_check-0.9.5/LICENSE-MIT) |
| `windows-link` | 0.2.1 | Non relevée : sources absentes du cache | Cible Windows, non compilée pour ce M3 | Aucune copie locale |
| `zmij` | 1.0.23 | `MIT` | Bibliothèque du graphe cible M3 | [LICENSE-MIT](licenses/rust/zmij-1.0.23/LICENSE-MIT) |

`windows-link` apparaît dans le verrouillage via la branche Windows de
`libloading`. Il n'a pas été compilé pour la cible Apple M3 et ses sources ne
sont pas présentes dans le cache local. Sa licence n'est donc pas déduite ni
recopiée depuis une autre version. Une distribution Windows devra compléter
son propre inventaire à partir des sources et du binaire réellement construits.

## Bibliothèque standard Rust

Le dossier [rust-toolchain](licenses/rust-toolchain/) contient les notices de
la bibliothèque standard livrées avec la chaîne locale :
[COPYRIGHT-library.html](licenses/rust-toolchain/COPYRIGHT-library.html),
[LICENSE-APACHE](licenses/rust-toolchain/LICENSE-APACHE),
[LICENSE-MIT](licenses/rust-toolchain/LICENSE-MIT) et les textes disponibles sous
`licenses/`. Ce relevé conserve les notices et expressions de licence du
fournisseur ; la présence d'un texte dans ce recueil n'affirme pas que tous les
composants qu'il décrit sont liés au binaire M3.

Le recueil `COPYRIGHT.html` couvrant l'ensemble du compilateur et des outils
n'est pas joint : ces exécutables de développement ne sont pas distribués
avec Mailleur. Aucune copie de Rust, Cargo ou de leur chaîne binaire n'est
ajoutée par cette opération.

## Provenance et limites

Le fichier [INVENTORY.json](licenses/rust/INVENTORY.json) consigne, pour chaque
copie, son chemin source dans le cache local, sa longueur et son empreinte
SHA-256 ; les octets ont été comparés lors de la copie. Le cache utilisé est
`.toolchain/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/` et les notices
Rust proviennent de la chaîne `stable-aarch64-apple-darwin` locale. Ces chemins
servent à documenter l'origine, ils ne sont pas requis chez le destinataire.

Il s'agit d'un **inventaire technique**, pas d'une analyse juridique exhaustive
de toutes les conditions de distribution. Les bibliothèques natives chargées
à l'exécution, dont Gmsh/OpenCASCADE, ainsi que Python, Qt et les convertisseurs,
relèvent de l'inventaire distinct [THIRD_PARTY.md](THIRD_PARTY.md). Les notices
spécifiques des binaires tiers réellement redistribués doivent également être
conservées. Toute modification des dépendances, de la cible ou de la chaîne
nécessite de reprendre ce relevé pour la nouvelle construction.
