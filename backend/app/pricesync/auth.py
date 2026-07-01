"""Interactive authentication (PRD §6.1) — authorization code flow with PKCE,
confidential client. Certificate credential preferred; client secret is a
fallback only. The access token is used once per fetch and discarded; no refresh
token is ever persisted (SEC-3).

MSAL for Python manages PKCE (code_verifier/challenge) and state via
initiate_auth_code_flow / acquire_token_by_auth_code_flow.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from .config import AUTHORITY, TOKEN_SCOPE, PriceSyncConfig

# In-memory, short-lived login flows keyed by OAuth state. Never persisted.
_PENDING_FLOWS: dict[str, dict] = {}


class AuthError(RuntimeError):
    pass


def _client_credential(cfg: PriceSyncConfig):
    """Build an MSAL client_credential: cert dict (preferred) or secret string.
    The certificate PEM (private key + cert) comes from the encrypted store."""
    if cfg.client_cert_pem:
        pem = cfg.client_cert_pem.encode()
        private_key = serialization.load_pem_private_key(pem, password=None)
        cert = x509.load_pem_x509_certificate(pem)
        thumbprint = cert.fingerprint(hashes.SHA1()).hex()
        return {
            "private_key": private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode(),
            "thumbprint": thumbprint,
            "public_certificate": cert.public_bytes(serialization.Encoding.PEM).decode(),
        }
    if cfg.client_secret:
        return cfg.client_secret
    raise AuthError("No client credential configured. Set a certificate or client secret in Settings.")


def _app(cfg: PriceSyncConfig):
    # Imported lazily so the module loads even if msal isn't installed yet.
    from msal import ConfidentialClientApplication

    # When the tenant isn't known yet, sign in against `organizations`; the tenant
    # is then read from the token's tid claim and persisted.
    tenant = cfg.tenant_id or "organizations"
    return ConfidentialClientApplication(
        client_id=cfg.client_id,
        authority=AUTHORITY.format(tenant_id=tenant),
        client_credential=_client_credential(cfg),
    )


def begin_login(cfg: PriceSyncConfig, redirect_uri: str) -> str:
    """Start an auth-code + PKCE flow. Returns the authorization URL to redirect
    the user's browser to. The flow (incl. PKCE verifier + state + redirect_uri)
    is stashed in memory keyed by state until the callback."""
    if not cfg.auth_configured:
        raise AuthError("Price sync is not configured (need client id, a view, and a credential).")
    if not redirect_uri:
        raise AuthError("No redirect URI available.")
    flow = _app(cfg).initiate_auth_code_flow(
        scopes=[TOKEN_SCOPE], redirect_uri=redirect_uri
    )
    if "auth_uri" not in flow or "state" not in flow:
        raise AuthError("Failed to initiate authorization code flow.")
    _PENDING_FLOWS[flow["state"]] = flow
    return flow["auth_uri"]


def redeem_code(cfg: PriceSyncConfig, auth_response: dict) -> tuple[str, dict]:
    """Exchange the callback's authorization code for an access token.

    Returns (access_token, id_token_claims). The token is NOT stored; the caller
    uses it once. The claims carry `tid` (tenant), `preferred_username`, `name`.
    """
    state = auth_response.get("state")
    flow = _PENDING_FLOWS.pop(state, None)
    if flow is None:
        raise AuthError("Unknown or expired login state. Start the refresh again.")
    result = _app(cfg).acquire_token_by_auth_code_flow(flow, auth_response)
    if "access_token" not in result:
        desc = result.get("error_description") or result.get("error") or "unknown error"
        raise AuthError(f"Token exchange failed: {desc}")
    return result["access_token"], result.get("id_token_claims", {}) or {}


def pending_count() -> int:
    return len(_PENDING_FLOWS)
