"""A marked Assistant installation with the production path validator intact."""

from sicopaths import IDENTITY_BYTES


def assistant_installation(root, monkeypatch):
    from cadai import installation

    for relative in ("bin", "tools/common", "tools/ai/python/cadai", "etc/config"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    (root / "etc/config/sico-install.json").write_bytes(IDENTITY_BYTES)
    monkeypatch.setattr(installation, "__file__", str(root / "tools/ai/python/cadai/installation.py"))
    monkeypatch.setenv("SICO_HOME", str(root))
    monkeypatch.delenv("CAD_HOME", raising=False)
    return root
