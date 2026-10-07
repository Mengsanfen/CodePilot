"""Shared redaction and minimal environments for generated code."""
from __future__ import annotations

import os
import re


def redact(value: str) -> str:
    for name, secret in os.environ.items():
        if re.search(r"TOKEN|SECRET|PASSWORD|API_KEY|DATABASE_URL", name) and len(secret) > 5:
            value = value.replace(secret, "[REDACTED]")
    return value


def command_environment() -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
               "LANG", "LC_ALL", "PYTHONUTF8", "NUMBER_OF_PROCESSORS"}
    result = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    result.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never",
                  GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                  GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="credential.helper", GIT_CONFIG_VALUE_0="")
    return result
