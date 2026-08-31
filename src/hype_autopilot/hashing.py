from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, date, datetime
from decimal import Decimal, ROUND_HALF_EVEN
from enum import Enum
from typing import Any

from pydantic import BaseModel

_QUANTUM = Decimal("0.0000000001")


def _decimal(value: float | Decimal) -> Decimal:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("NaN and Infinity are not canonicalizable")
    quantized = Decimal(str(value)).quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)
    return abs(quantized) if quantized == 0 else quantized


def _normalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetimes are not canonicalizable")
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (float, Decimal)):
        return _decimal(value)
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _encode(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, ".10f")
    if isinstance(value, list):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            f"{_encode(key)}:{_encode(value[key])}" for key in sorted(value)
        ) + "}"
    raise TypeError(f"unsupported canonical JSON value: {type(value)!r}")


def canonical_json(value: Any) -> str:
    return _encode(_normalize(value))


def sha256_canonical(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
