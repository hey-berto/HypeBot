from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, model_validator

from hype_autopilot.hashing import sha256_canonical
from hype_autopilot.phase2.config import (
    ACTIVATION_PHRASE,
    Phase2Config,
    config_manifest_fields,
)


class Phase2Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    manifest_id: str
    experiment_id: str
    phase2_epoch_id: str
    created_at: datetime
    activation_timestamp: datetime
    authorization_phrase: str
    git_commit_hash: str
    config_hash: str
    prompt_hash: str
    output_schema_hash: str
    database_schema_hash: str
    frozen_contract: dict[str, Any]
    manifest_hash: str

    @model_validator(mode="after")
    def validate_identity(self) -> Phase2Manifest:
        payload = self.model_dump(
            mode="python", exclude={"manifest_id", "manifest_hash"}
        )
        expected_hash = sha256_canonical(payload)
        expected_id = str(uuid5(NAMESPACE_URL, f"phase2-manifest:{expected_hash}"))
        if self.manifest_hash != expected_hash or self.manifest_id != expected_id:
            raise ValueError(
                "Phase 2 manifest identity does not match its immutable payload"
            )
        return self


def build_activation_manifest(
    *,
    config: Phase2Config,
    experiment_id: str,
    activation_timestamp: datetime,
    authorization: str,
    git_commit_hash: str,
    config_hash: str,
    prompt_hash: str,
    output_schema_hash: str,
    database_schema_hash: str,
) -> Phase2Manifest:
    config.assert_activation(authorization)
    if activation_timestamp.tzinfo is None:
        raise ValueError("activation timestamp must be timezone-aware")
    activated_at = activation_timestamp.astimezone(UTC)
    payload = {
        "experiment_id": experiment_id,
        "phase2_epoch_id": config.phase2_epoch_id,
        "created_at": activated_at,
        "activation_timestamp": activated_at,
        "authorization_phrase": ACTIVATION_PHRASE,
        "git_commit_hash": git_commit_hash,
        "config_hash": config_hash,
        "prompt_hash": prompt_hash,
        "output_schema_hash": output_schema_hash,
        "database_schema_hash": database_schema_hash,
        "frozen_contract": config_manifest_fields(config),
    }
    digest = sha256_canonical(payload)
    return Phase2Manifest(
        manifest_id=str(uuid5(NAMESPACE_URL, f"phase2-manifest:{digest}")),
        manifest_hash=digest,
        **payload,
    )
