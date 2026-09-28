"""Versioned native storage profiles admitted for read-only archival."""

import hashlib
import json

# Hashes cover all SQLite objects and successful sqlx migration checksums. Unknown
# profiles require a separately qualified reader; filename versions are insufficient.
_LEGACY_0154 = {
    "goals_1.sqlite": (
        "47b2811083cce8022181dcc319aaf937560cb7df5d936f1aa5f897e35b5c6d3d",
        "fca38718cf64d177050602a658abd2433a2015e2851bd27d6e8aaa64a72fd50b"),
    "logs_2.sqlite": (
        "c8fb28dc9cbb5acb2916ff171ce58a374084ce2fb7c89a1c8c989594a3513a48",
        "f33c6cea797934af12b31b58d613153e19d22b28d1130bf92641da63d18eeefa"),
    "memories_1.sqlite": (
        "bc751d6809ae9db8d6cb783d05022dd30fe22688c44a0069349d5a6a1444b78c",
        "37336071526a8ebee99ed242ad54ef10aa8d0bb2312894e1d63fb131d35f3d48"),
    "queue_1.sqlite": (
        "102a82c0ebbc433e187ff17854864d9fa963b73ff830ea9929c1e9d6c53044df",
        "5e819f89b116bfdae8841c1a403d6cab74169c8da76f967506173f7b2b2bc701"),
    "state_5.sqlite": (
        "0591af00a9e8024abae1e227c659edc9cbb5a73705fd118535d957d0863c1363",
        "cb8b3c36bacd3b1bf04c0a80b125ac1c5f20b331363a22fcbc572b515e803a2b"),
    "thread_history_1.sqlite": (
        "8651c0335417b82af738a434bca12d707f8fc8352462ff4d0108ceddca512e3a",
        "e666687e3e71c0be1acdb9f27bc61fce297ae7e0aee39a54c962e3a7529ec56f"),
}


# Legacy admission remains qualified independently; never infer a profile from a
# SQLite filename or accept a version range. Keep until 0.154 archives are retired.
PROFILES = {
    "0.154.0": _LEGACY_0154,
    "0.156.1": {
        **_LEGACY_0154,
        "state_5.sqlite": (
            "6e2413a16e1d0441f919523917685037ad2d299c30c00989d1312111afb7da26",
            "2fd387ccd2563d360e69babca333d43acf5a73ddd5dfbe1381128cc3bb15a5e5"),
        "memories_1.sqlite": (
            "cf78c83293ee523b93ebcb76cc53a12a7d2c13dd4d33f05da17337b2acc6d9f8",
            "33e24327fdcbbb7a2d244c72ddecf67bb38d8b0035c3064c32928df53aff5880"),
    },
}
DATABASES = frozenset(_LEGACY_0154)


def home_version(connection):
    versions = connection.execute("SELECT DISTINCT cli_version FROM threads").fetchall()
    if len(versions) != 1 or versions[0][0] not in PROFILES:
        raise ValueError("Mixed or unqualified native home versions require review")
    return versions[0][0]


def digest(rows):
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def validate_profile(connection, name, version):
    if version not in PROFILES or name not in DATABASES:
        raise ValueError("Unqualified native SQLite version")
    expected = PROFILES[version][name]
    schema = connection.execute("SELECT type, name, tbl_name, sql FROM sqlite_schema "
                                "ORDER BY type, name").fetchall()
    if digest(schema) != expected[0]:
        raise ValueError("Unqualified native SQLite schema")
    migrations = connection.execute("SELECT version, success, hex(checksum) FROM "
                                    "_sqlx_migrations ORDER BY version").fetchall()
    if any(row[1] != 1 for row in migrations) or digest(migrations) != expected[1]:
        raise ValueError("Unqualified native SQLite migration history")
