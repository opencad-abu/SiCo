"""Read the shared SiCo model identity for the terminal backend."""

from .env_names import value

RETIRED_MODEL_KEYS = ("SICO_CODEX_MODEL", "SICO_CODEX_API_BASE_URL", "SICO_CODEX_API_KEY")


def settings(environment):
    result = {name: str(value(environment, name, "")).strip()
              for name in ("API_BASE_URL", "MODEL", "API_KEY")}
    if not any(name in environment for name in
               ("SICO_API_BASE_URL", "SICO_API_URL", "SICO_MODEL", "SICO_API_KEY")):
        if any(environment.get(name, "").strip() for name in RETIRED_MODEL_KEYS):
            raise ValueError("Use shared SICO_API_BASE_URL, SICO_MODEL and SICO_API_KEY; "
                             "terminal-only SICO_CODEX model settings are no longer read")
    return result


def publish_gateway(environment, base_url, api_key):
    """Only the private terminal child receives the local proxy identity."""
    environment["SICO_API_BASE_URL"] = base_url
    environment["SICO_API_KEY"] = api_key
    environment.pop("SICO_API_URL", None)
    for name in RETIRED_MODEL_KEYS:
        environment.pop(name, None)
