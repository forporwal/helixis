"""Helixis — an automation agent that measurably improves across runs.

The loop: execute graded tasks, capture raw trajectories, distill failures into
persistent skills, inject those skills into the next run, measure the delta.
"""

from __future__ import annotations

from .config import SETTINGS, Settings, load_settings
from .distiller import Distiller
from .runner import EpochRunner, Experiment, Manifest
from .store import EpisodeResult, EpisodeStore
from .wiki import ExperienceWiki, Skill

__all__ = [
    "SETTINGS",
    "Distiller",
    "EpisodeResult",
    "EpisodeStore",
    "EpochRunner",
    "Experiment",
    "ExperienceWiki",
    "Manifest",
    "Settings",
    "Skill",
    "load_settings",
]

__version__ = "0.1.0"
