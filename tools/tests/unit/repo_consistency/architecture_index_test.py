"""Unit tests for ARCHITECTURE.md index-to-record consistency.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

from pathlib import Path

from modules.repo_consistency.architecture_index import find_architecture_index_violations


def _write_record(root: Path, subdir: str, filename: str, title: str) -> None:
    record_dir = root / "docs" / subdir
    record_dir.mkdir(parents=True, exist_ok=True)
    (record_dir / filename).write_text(f"# {title}\n\nBody.\n", encoding="utf-8")


def _write_architecture(root: Path, adr_row: str = "") -> None:
    content = (
        "# Brain Overview\n\n"
        "## Architecture Decision Records\n\n"
        "| # | Decision | Trigger condition | Summary |\n"
        "|---|----------|-------------------|---------|\n"
        f"{adr_row}"
        "\n## Crosscutting Concepts\n\n"
        "| # | Concept | Trigger condition | Summary |\n"
        "|---|----------|--------------------|---------|\n"
    )
    (root / "ARCHITECTURE.md").write_text(content, encoding="utf-8")


class TestArchitectureIndexConsistency:
    """Feature: Architecture Index Consistency"""

    def test_index_row_matching_its_record_is_not_reported(self, tmp_path):
        # Scenario: Index row matching its record's filename and heading is not reported
        _write_record(tmp_path, "adr", "0001-example.md", "Example Decision")
        _write_architecture(tmp_path, adr_row="| [0001](docs/adr/0001-example.md) | Example Decision | trig | sum |\n")

        violations = find_architecture_index_violations(tmp_path)

        assert violations == []

    def test_index_row_with_a_stale_title_is_reported(self, tmp_path):
        # Scenario: Index row with a stale title is reported
        _write_record(tmp_path, "adr", "0001-example.md", "Renamed Decision")
        _write_architecture(tmp_path, adr_row="| [0001](docs/adr/0001-example.md) | Old Decision Title | trig | sum |\n")

        violations = find_architecture_index_violations(tmp_path)

        assert len(violations) == 1
        assert "Old Decision Title" in violations[0].message
        assert "Renamed Decision" in violations[0].message


class TestRepositoryArchitectureIndexConsistency:
    """Feature: Architecture Index Consistency"""

    def test_current_repositorys_architecture_index_matches_its_records(self, repo_root):
        # Scenario: Current repository's architecture index matches its records
        violations = find_architecture_index_violations(repo_root)

        assert violations == [], "\n".join(str(v) for v in violations)
