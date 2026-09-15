from __future__ import annotations

import json
from pathlib import Path


def load_token(path: Path | str | None, phone: str | None = None) -> str | None:
    if not path:
        return None
    path = Path(path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if phone and payload.get("phone") and payload["phone"] != phone:
        return None
    token = payload.get("token")
    return str(token) if token else None


def save_token(path: Path | str | None, phone: str | None, token: str) -> None:
    if not path:
        return
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"phone": phone, "token": token}, ensure_ascii=False),
            encoding="utf-8",
        )
        path.chmod(0o600)
    except OSError:
        pass
