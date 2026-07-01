"""Cloud Solution Provider authentication (Secure Application Model).

A one-time interactive partner consent (MFA, dedicated service account holding
Admin Agent / Sales Agent) produces a refresh token. The app stores that token
(encrypted) and exchanges it for an access token on the partner's behalf at fetch
time — no per-fetch browser redirect. Certificate credential preferred; client
secret is a fallback.

The refresh token is confidential and lives only in the encrypted secret store.
"""

from __future__ import annotations

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from .config import AUTHORITY, TOKEN_SCOPE, PriceSyncConfig


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
    raise AuthError("No app credential configured. Set a certificate or client secret in Settings.")


def _app(cfg: PriceSyncConfig):
    from msal import ConfidentialClientApplication

    return ConfidentialClientApplication(
        client_id=cfg.client_id,
        authority=AUTHORITY.format(tenant_id=cfg.tenant_id),
        client_credential=_client_credential(cfg),
    )


def acquire_access_token(cfg: PriceSyncConfig) -> tuple[str, str | None, dict]:
    """Exchange the stored refresh token for a price-sheet API access token.

    Returns (access_token, rotated_refresh_token_or_None, id_token_claims).
    The token is used once for the fetch and discarded; a rotated refresh token
    (if returned) should be persisted by the caller.
    """
    if not cfg.auth_configured:
        raise AuthError(
            "Price sync is not configured — need partner tenant, app id, a "
            "credential, and a consent refresh token."
        )
    result = _app(cfg).acquire_token_by_refresh_token(
        cfg.refresh_token, scopes=[TOKEN_SCOPE]
    )
    if "access_token" not in result:
        desc = result.get("error_description") or result.get("error") or "unknown error"
        raise AuthError(
            f"Token request failed: {desc}. The refresh token may be expired or "
            "revoked — re-run the partner consent to obtain a new one."
        )
    return (
        result["access_token"],
        result.get("refresh_token"),
        result.get("id_token_claims", {}) or {},
    )
