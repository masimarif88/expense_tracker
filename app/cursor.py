import base64
import hashlib
import hmac
import json
import uuid
from datetime import date, datetime
from typing import Any

from app.config import get_settings
from app.errors import ApiError


def filters_fingerprint(filters: dict[str, Any]) -> str:
    serialized = json.dumps(filters, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def encode_cursor(
    spent_on: date, expense_id: uuid.UUID, snapshot: datetime, fingerprint: str
) -> str:
    settings = get_settings()
    payload = {
        "spent_on": spent_on.isoformat(),
        "id": str(expense_id),
        "snapshot": snapshot.isoformat(),
        "filters": fingerprint,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(settings.jwt_secret.encode(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + signature).rstrip(b"=").decode()


def decode_cursor(cursor: str, fingerprint: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode())
        raw, supplied_signature = decoded[:-32], decoded[-32:]
        expected_signature = hmac.new(
            settings.jwt_secret.encode(), raw, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError
        payload = json.loads(raw)
        if payload["filters"] != fingerprint:
            raise ValueError
        payload["spent_on"] = date.fromisoformat(payload["spent_on"])
        payload["id"] = uuid.UUID(payload["id"])
        payload["snapshot"] = datetime.fromisoformat(payload["snapshot"])
        return payload
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ApiError(400, "invalid_cursor", "The pagination cursor is invalid") from exc
