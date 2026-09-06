from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hype_autopilot.review_bundle import (
    ChangeCategory,
    CheckRecord,
    ReviewBundleRequest,
    SensitiveMaterialError,
    generate_review_bundle,
    write_review_bundle,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def repository(tmp_path: Path, second_content: str = "safe change\n"):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "review")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    (repo / "artifact.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "artifact.txt")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "artifact.txt").write_text(second_content, encoding="utf-8")
    (repo / "extra.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "change")
    return repo, base, git(repo, "rev-parse", "HEAD")


def request(base: str, reviewed: str) -> ReviewBundleRequest:
    return ReviewBundleRequest(
        repository="https://example.invalid/repo",
        branch="review",
        base_commit=base,
        reviewed_commit=reviewed,
        generated_at=datetime(2026, 9, 6, tzinfo=UTC),
        category=ChangeCategory.ANALYSIS_TOOLING,
        purpose="Fixture review",
        architecture_impact="Adds isolated tooling",
        research_defining_impact="NONE",
        operational_impact="NONE",
        config_schema_prompt_model_changes=("NONE",),
        migrations_or_persistent_state_changes=("NONE",),
        checks=(CheckRecord(command="pytest -q", passed=3, failed=0, status="PASS"),),
        known_limitations=("Fixture only",),
        security_implications=("No credentials",),
        focused_paths=("artifact.txt",),
        reviewer_questions=("Is the fixture clear?",),
    )


def test_bundle_captures_exact_git_identity_files_checks_and_category(tmp_path):
    repo, base, reviewed = repository(tmp_path)
    bundle = generate_review_bundle(repo, request(base, reviewed))
    assert f"Base commit: `{base}`" in bundle
    assert f"Reviewed commit: `{reviewed}`" in bundle
    assert "- artifact.txt" in bundle
    assert "- extra.py" in bundle
    assert "3 passed, 0 failed" in bundle
    assert "Change category: `ANALYSIS_TOOLING`" in bundle


def test_bundle_is_deterministic_for_fixed_request(tmp_path):
    repo, base, reviewed = repository(tmp_path)
    frozen = request(base, reviewed)
    assert generate_review_bundle(repo, frozen) == generate_review_bundle(repo, frozen)


def test_sensitive_patch_is_withheld_before_output_is_created(tmp_path):
    repo, base, reviewed = repository(
        tmp_path,
        "OPENAI_API_KEY=sk-proj-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n",
    )
    output = tmp_path / "unsafe.md"
    with pytest.raises(SensitiveMaterialError, match="withheld"):
        write_review_bundle(repo, request(base, reviewed), output)
    assert not output.exists()


def test_focused_patch_must_be_part_of_exact_git_change(tmp_path):
    repo, base, reviewed = repository(tmp_path)
    invalid = request(base, reviewed).model_copy(
        update={"focused_paths": ("not-changed.txt",)}
    )
    with pytest.raises(ValueError, match="not changed"):
        generate_review_bundle(repo, invalid)
