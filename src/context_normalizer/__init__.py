"""Transparent, deterministic context normalization for AI coding clients."""

__version__ = "1.0.0"

from .config import Rule
from .normalize import normalize_text

__all__ = ["Rule", "__version__", "normalize_text"]
