---
description: Protect generated skill Python scripts from direct modifications.
applyTo: "skills/**/scripts/**/*.py"
---

# Generated skill scripts

- MUST treat every matching Python file as generated and read-only.
- MUST NOT create, edit, rename, delete, or reformat generated scripts directly, even if faster or simpler.
- MUST edit the canonical launcher or supervisor under `scripts/babysit/`, or the corresponding factory module under `software_factory/`.
- MUST regenerate the skill copy with `python scripts/sync_babysit_skill.py` and verify it with `--check`.
- MUST fix the canonical source or synchronizer when generation fails; never patch the generated output.
- MUST NOT bypass the pre-commit hook or CI synchronization check.
- MAY read generated scripts for debugging, review, and investigation.
