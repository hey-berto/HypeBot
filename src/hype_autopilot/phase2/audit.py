from __future__ import annotations

import os
import re


class SensitiveCredentialMaterial(ValueError):
    """Raised before provider output containing credential material is persisted."""


_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)\b(?:authorization|proxy-authorization)\s*:\s*bearer\s+\S+"),
    re.compile(r"(?i)\bOPENAI_API_KEY\s*[:=]\s*\S+"),
    re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_CREDENTIAL_ENV_SUFFIXES = (
    "_API_KEY",
    "_TOKEN",
    "_SECRET",
    "_" + "PRIVATE" + "_KEY",
)


def validate_raw_provider_plaintext(value: str) -> str:
    """Return exact output only when it contains no credential-shaped material."""

    for pattern in _CREDENTIAL_PATTERNS:
        if pattern.search(value):
            raise SensitiveCredentialMaterial(
                "provider output contains credential-shaped material"
            )
    for name, secret in os.environ.items():
        if (
            name.upper().endswith(_CREDENTIAL_ENV_SUFFIXES)
            and len(secret) >= 8
            and secret in value
        ):
            raise SensitiveCredentialMaterial(
                "provider output contains configured credential material"
            )
    return value
