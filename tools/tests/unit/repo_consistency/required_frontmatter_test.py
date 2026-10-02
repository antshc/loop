"""Unit tests for required agent/skill frontmatter fields.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

from pathlib import Path

from modules.repo_consistency.required_frontmatter import find_frontmatter_violations


def _write_skill(root: Path, name: str, frontmatter: str) -> None:
    skill_dir = root / "plugins" / "demo" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"{frontmatter}\n# {name}\nBody.\n", encoding="utf-8")


class TestAgentAndSkillFrontmatterCompleteness:
    """Feature: Agent And Skill Frontmatter Completeness"""

    def test_file_with_all_required_frontmatter_fields_is_not_reported(self, tmp_path):
        # Scenario: File with all required frontmatter fields is not reported
        _write_skill(tmp_path, "complete", "---\nname: complete\ndescription: does things.\n---")

        violations = find_frontmatter_violations(tmp_path)

        assert violations == []

    def test_file_missing_a_required_frontmatter_field_is_reported(self, tmp_path):
        # Scenario: File missing a required frontmatter field is reported
        _write_skill(tmp_path, "incomplete", "---\nname: incomplete\n---")

        violations = find_frontmatter_violations(tmp_path)

        assert len(violations) == 1
        assert violations[0].file.endswith("incomplete/SKILL.md")
        assert "description" in violations[0].message


class TestRepositoryFrontmatterConsistency:
    """Feature: Agent And Skill Frontmatter Completeness"""

    def test_current_repositorys_agents_and_skills_carry_required_frontmatter(self, repo_root):
        # Scenario: Current repository's agents and skills carry required frontmatter
        violations = find_frontmatter_violations(repo_root)

        assert violations == [], "\n".join(str(v) for v in violations)
