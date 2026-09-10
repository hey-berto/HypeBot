from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hype_autopilot.hashing import sha256_canonical

GENERATOR_VERSION = "CODE_REVIEW_BUNDLE_V1"


class ChangeCategory(StrEnum):
    RESEARCH_DEFINING = "RESEARCH_DEFINING"
    OPERATIONAL_ONLY = "OPERATIONAL_ONLY"
    ANALYSIS_TOOLING = "ANALYSIS_TOOLING"
    SAFETY_INFRASTRUCTURE = "SAFETY_INFRASTRUCTURE"
    DOCUMENTATION_ONLY = "DOCUMENTATION_ONLY"


class CheckRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command: str
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    status: str


class ReviewBundleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: str
    branch: str
    base_commit: str
    reviewed_commit: str
    generated_at: datetime
    category: ChangeCategory
    purpose: str
    architecture_impact: str
    research_defining_impact: str
    operational_impact: str
    config_schema_prompt_model_changes: tuple[str, ...]
    migrations_or_persistent_state_changes: tuple[str, ...]
    checks: tuple[CheckRecord, ...]
    known_limitations: tuple[str, ...]
    security_implications: tuple[str, ...]
    focused_paths: tuple[str, ...]
    reviewer_questions: tuple[str, ...]

    @model_validator(mode="after")
    def validate_request(self) -> ReviewBundleRequest:
        if (
            self.generated_at.tzinfo is None
            or self.generated_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("generated_at must be UTC")
        for value in (self.base_commit, self.reviewed_commit):
            if not re.fullmatch(r"[0-9a-f]{40}", value):
                raise ValueError("commit identities must be full lowercase SHAs")
        if not self.focused_paths:
            raise ValueError("at least one focused path is required")
        return self


class SensitiveMaterialError(RuntimeError):
    pass


_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?" + "PRIVATE" + r" KEY-----"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"),
    re.compile(
        r"(?im)^\s*(?:OPENAI_API_KEY|HYPERLIQUID_"
        + "PRIVATE"
        + r"_KEY|"
        + "PRIVATE"
        + r"_KEY)\s*=\s*['\"]?(?!REDACTED|PLACEHOLDER|\$\{)[^\s'\"]{12,}"
    ),
)


def assert_no_sensitive_material(value: str) -> None:
    for pattern in _SECRET_PATTERNS:
        if pattern.search(value):
            raise SensitiveMaterialError(
                "sensitive-material scan failed; review bundle was withheld"
            )


def load_review_bundle_request(path: str | Path) -> ReviewBundleRequest:
    return ReviewBundleRequest.model_validate(
        yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    )


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _section_list(values: tuple[str, ...]) -> str:
    return "\n".join(f"- {value}" for value in values) if values else "- NONE"


def generate_review_bundle(repo: str | Path, request: ReviewBundleRequest) -> str:
    root = Path(repo).resolve()
    base = _git(root, "rev-parse", request.base_commit).strip()
    reviewed = _git(root, "rev-parse", request.reviewed_commit).strip()
    branch = _git(root, "branch", "--show-current").strip()
    if base != request.base_commit or reviewed != request.reviewed_commit:
        raise ValueError("Git commit identity did not resolve exactly")
    if branch != request.branch:
        raise ValueError(f"branch mismatch: expected {request.branch}, found {branch}")

    changed_files = tuple(
        line
        for line in _git(
            root, "diff", "--name-only", request.base_commit, request.reviewed_commit
        ).splitlines()
        if line
    )
    missing = sorted(set(request.focused_paths) - set(changed_files))
    if missing:
        raise ValueError(f"focused paths are not changed files: {', '.join(missing)}")
    diff = _git(
        root,
        "diff",
        "--no-ext-diff",
        "--unified=5",
        request.base_commit,
        request.reviewed_commit,
        "--",
        *request.focused_paths,
    )
    request_json = request.model_dump_json()
    assert_no_sensitive_material(request_json)
    assert_no_sensitive_material(diff)
    if len(diff.encode("utf-8")) > 180_000:
        raise ValueError("focused patch exceeds 180 KiB; narrow focused_paths")

    dependency_changes = tuple(
        path
        for path in changed_files
        if path in {"pyproject.toml", "uv.lock", "requirements.txt", "poetry.lock"}
    )
    generator_commit = _git(
        root,
        "log",
        "-1",
        "--format=%H",
        "--",
        "src/hype_autopilot/review_bundle.py",
        "src/hype_autopilot/tooling_cli.py",
    ).strip()
    if not generator_commit:
        generator_commit = _git(root, "rev-parse", "HEAD").strip()
    content_hash = sha256_canonical(
        {
            "request": request,
            "changed_files": changed_files,
            "focused_diff": diff,
            "generator_version": GENERATOR_VERSION,
            "generator_commit": generator_commit,
        }
    )
    checks = "\n".join(
        f"- `{item.command}` — {item.status}; {item.passed} passed, {item.failed} failed"
        for item in request.checks
    )
    markdown = f"""# HYPE Autopilot code review bundle

## Immutable identity

- Generator: `{GENERATOR_VERSION}` at `{generator_commit}`
- Bundle content hash: `{content_hash}`
- Repository: `{request.repository}`
- Branch: `{branch}`
- Base commit: `{base}`
- Reviewed commit: `{reviewed}`
- Generated at: `{request.generated_at.astimezone(UTC).isoformat()}`
- Change category: `{request.category.value}`

## Review summary

**Purpose:** {request.purpose}

**Architecture impact:** {request.architecture_impact}

**Research-defining impact:** {request.research_defining_impact}

**Operational/runtime impact:** {request.operational_impact}

## Changed files

{_section_list(changed_files)}

## Config, schema, prompt and model identities

{_section_list(request.config_schema_prompt_model_changes)}

## Dependencies

{_section_list(dependency_changes)}

## Migrations and persistent state

{_section_list(request.migrations_or_persistent_state_changes)}

## Verification

{checks or "- No checks declared"}

## Known limitations and unresolved issues

{_section_list(request.known_limitations)}

## Security and secrets

{_section_list(request.security_implications)}

The generator scanned its structured request and focused diff for private-key,
provider-key and common token signatures before emitting this artifact. It does
not read `.env` files.

## Focused unified patch

```diff
{diff.rstrip()}
```

## Reviewer questions

{_section_list(request.reviewer_questions)}
"""
    assert_no_sensitive_material(markdown)
    return markdown


def write_review_bundle(
    repo: str | Path, request: ReviewBundleRequest, output: str | Path
) -> Path:
    markdown = generate_review_bundle(repo, request)
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(markdown, encoding="utf-8")
    return destination
