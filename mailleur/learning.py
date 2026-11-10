"""Retired Python trainer; training and inference now run in Rust only."""
from __future__ import annotations

from .geometry import LegacyNumericalAPIError

STRATEGIES = ("quality", "shortest", "balanced", "first")
FEATURE_NAMES = ()
FEATURE_COUNT = 0
MODEL_VERSION = 0


def _retired(*args, **kwargs):
    raise LegacyNumericalAPIError(
        "L'entraînement Python du prototype a été retiré en rc4. "
        "Utiliser NativeSession.learn/train ou les commandes corpus de l'atelier ; "
        "collecte, descripteurs, entraînement et prédictions sont exécutés par Rust."
    )


extract_features = collect_corpus = split_corpus = train_guide = _retired


class Guide:
    """Import-compatible tombstone; no weights are evaluated in Python."""
    def __init__(self, *args, **kwargs):
        _retired()

    load = save = select = scores = _retired
