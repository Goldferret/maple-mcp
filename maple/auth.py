"""MAPLE authentication — auto-generated device tokens for session identity."""

import os
import re
import uuid
from pathlib import Path


CREDENTIALS_FILE = Path.home() / ".maple" / "credentials"
_UUID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')


def get_or_create_token() -> str:
    """Get the device token, creating one on first run.
    
    Tokens are UUIDv4 strings stored in ~/.maple/credentials.
    Used as bearer tokens for MCP server auth, keying session state.
    
    Returns:
        The device's unique token string.
    """
    if CREDENTIALS_FILE.exists():
        token = CREDENTIALS_FILE.read_text().strip()
        if token and is_valid_token(token):
            return token

    # Generate new token
    token = str(uuid.uuid4())
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(token)

    # Set file permissions to owner-only (0600)
    try:
        os.chmod(CREDENTIALS_FILE, 0o600)
    except OSError:
        pass  # Windows or permission issues — best effort

    return token


def is_valid_token(token: str) -> bool:
    """Validate that a token is a well-formed UUIDv4.
    
    Prevents arbitrary/malformed strings from becoming dict keys.
    """
    return bool(_UUID_PATTERN.match(token.lower()))
