---
name: init-harness
description: Create or update the harness's repository-level `harness` skill (`.github/skills/harness/`) and its pull command. Run once, from the harness root, for a multi-repo/wrapping workspace.
disable-model-invocation: true
---

# Setup Harness

Run from the harness root (= CWD). Sets up `.github/skills/harness/`: copies the `harness` skill from template, ensures its settings file exists, installs its pull command. **MUST NOT** migrate an existing root-level `.harness.json.user` — clean installs only.

## Workflow

**1 — Copy the `harness` skill template.** `skillDir :=` parent of this `SKILL.md`'s absolute path from context. Copy `<skillDir>/harness.SKILL.template.md` → CWD `.github/skills/harness/SKILL.md`, always overwriting.

```bash
python3 -c "
from pathlib import Path
import shutil
dest = Path('.github/skills/harness/SKILL.md')
dest.parent.mkdir(parents=True, exist_ok=True)
shutil.copy('<skillDir>/harness.SKILL.template.md', dest)
"
```

**2 — Ensure the settings file exists.** `settingsPath := .github/skills/harness/.harness.json.user` (CWD). If absent, create it holding `{}`. Never overwrite an existing file.

**3 — Gitignore the settings file only.**

```bash
git check-ignore -q "$settingsPath" || echo "NOT IGNORED"
```

`NOT IGNORED` → append `.harness.json.user` line to CWD `.gitignore` (create if needed). Never ignore the folder — its `SKILL.md` is checked in.

**4 — Install the pull command.** Copy `<skillDir>/scripts/pull-repos.py` → CWD `.github/skills/harness/pull-repos.py`, always overwriting. It is the only reader of `repos` and reads its sibling `.harness.json.user` directly.

**5 — Report.** Skill copied; settings-file status (created/already present); `.gitignore` status; pull-command status (created/overwritten).

## Rules

- Generated skill only under CWD `.github/skills/harness/`, never a plugin folder.
- `SKILL.md` and `pull-repos.py` always overwritten; the settings file never overwritten.
- No migration of a root-level `.harness.json.user` — report it if found, but do not move or read it.

## Gotchas

- **MUST NOT** `find`/`grep`/`ls -R` for this skill's directory or template; derive from this `SKILL.md`'s absolute path in context.
- Resolve paths from CWD, never an env var; run from the harness root.

## Verification

No test suite for the setup steps (unit tests cover `pull-repos.py` directly). Verify manually on a repo with no `.github/skills/harness/`: the copied `SKILL.md` is byte-identical to the template; `pull-repos.py` is byte-identical to its source; `.harness.json.user` is `{}` and gitignored; rerunning leaves the settings file untouched but refreshes `SKILL.md`/`pull-repos.py`.
