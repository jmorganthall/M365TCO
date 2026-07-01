"""Price Sheet Sync and Freshness module.

Acquires the Microsoft Partner Center price sheet through an interactive
authorization-code + PKCE login (confidential client), stores it on the
persistent volume with a metadata sidecar, and classifies its freshness
locally with no auth and no API call.

Two behaviours are decoupled by design (PRD §1):
  - Age check: automatic, local, no auth, no API call  (freshness.py)
  - Fetch:     on demand, one interactive login, one API call  (auth.py + fetch.py)

No user refresh token is ever persisted (SEC-3). The access token is used once
per fetch and discarded.
"""

from .config import PriceSyncConfig, load_config
from .freshness import Freshness, classify

__all__ = ["PriceSyncConfig", "load_config", "Freshness", "classify"]
