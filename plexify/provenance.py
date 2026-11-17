from __future__ import annotations

import hashlib
from pathlib import Path


def machine_instance_id() -> str:
    """Return a non-secret stable identifier for this OS installation."""
    for path in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return hashlib.sha256(f"ingestion-suite\0{value}".encode()).hexdigest()
    raise RuntimeError("no stable machine identity is available for publication provenance")
