"""Unit tests for skill-invocation resolution.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

from pathlib import Path

from modules.repo_consistency.skill_invocations import find_dangling_skill_invocations


def _write_skill(root: Path, plugin: str, name: str, body: str) -> None:
    skill_dir = root / "plugins" / plugin / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill.\n---\n\n{body}\n", encoding="utf-8"
    )


class TestSkillInvocationResolution:
    """Feature: Skill Invocation Resolution"""

    def test_skill_invocation_naming_an_existing_skill_is_not_reported(self, tmp_path):
        # Scenario: Skill invocation naming an existing skill is not reported
        _write_skill(tmp_path, "demo", "target", "# Target\nDoes things.")
        _write_skill(tmp_path, "demo", "caller", "Run `/target` skill.")

        violations = find_dangling_skill_invocations(tmp_path)

        assert violations == []

    def test_skill_invocation_naming_a_nonexistent_skill_is_reported(self, tmp_path):
        # Scenario: Skill invocation naming a nonexistent skill is reported
        _write_skill(tmp_path, "demo", "caller", "Run `/ghost-skill` skill.")

        violations = find_dangling_skill_invocations(tmp_path)

        assert len(violations) == 1
        assert violations[0].file.endswith("caller/SKILL.md")
        assert "ghost-skill" in violations[0].message

    def test_backticked_filesystem_paths_are_not_skill_invocations(self, tmp_path):
        # Scenario: Backticked filesystem paths are not skill invocations
        _write_skill(
            tmp_path,
            "demo",
            "caller",
            "Never root a command at `/usr`, `/opt`, `/etc`, or `/home`.",
        )

        violations = find_dangling_skill_invocations(tmp_path)

        assert violations == []


class TestRepositorySkillInvocationConsistency:
    """Feature: Skill Invocation Resolution"""

    def test_current_repository_has_no_dangling_skill_invocations(self, repo_root):
        # Scenario: Current repository has no dangling skill invocations
        violations = find_dangling_skill_invocations(repo_root)

        assert violations == [], "\n".join(str(v) for v in violations)
