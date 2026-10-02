---
name: harness
description: Resolve the Harness Repo Path and this repo's per-developer Harness user settings (`.harness.json.user`). Run first from ralph, crew, and wf skills that need the harness root or its configured repositories.
---

# Harness

Resolution gate for every skill that needs the Harness Repo Path. Sole owner of locating and parsing `.harness.json.user` (gitignored, sibling of this `SKILL.md`); other skills MUST NOT re-implement it.

## Resolve

1. **Locate settings.** From this skill's base directory run `python3 -c "from pathlib import Path; print(Path('.harness.json.user').resolve())"` → `settingsPath` (absolute; printed even when the file is absent).
2. **Derive the Harness Repo Path.** From this skill's base directory run `python3 -c "from pathlib import Path; print(Path('../../..').resolve())"` → `harnessRepoPath`. This skill's own location — three folders below the harness root — is the anchor; never search the filesystem or walk up from cwd.
3. **Read settings.** From this skill's base directory run `python3 -c "from pathlib import Path; print(Path('.harness.json.user').read_text())"`.
   - Not found → **exit non-zero**, report `missing` on stderr. Caller falls back to cwd as `HARNESS_REPO_PATH`.
   - Not valid JSON, or not a JSON object → **exit non-zero**, report `invalid` on stderr with the parse error. Caller stops and reports.
   - Found and valid → every top-level key is emitted verbatim.
4. **Report** `harnessRepoPath` plus every key read in step 3 (e.g. `repos`) before the caller's operation.

**Config handoff:** scripts in this folder (e.g. `pull-repos.py`) read the settings file themselves via their own location — never rebuild the path.

## Pull

Run `python3 pull-repos.py [name[:branch] ...]` from this folder to bring every repository listed in `repos` up to date. Omit arguments to pull every configured repository.

## Gotchas

- **MUST NOT** search the filesystem (`find`/`grep`/`ls -R`/`os.walk`) for this skill's directory or `.harness.json.user`. Base dir = parent of the `SKILL.md` path given in context; the settings file and `pull-repos.py` are its siblings.
- **Long absolute paths in `python3 -c` or heredocs get corrupted by terminal line-wrapping.** `cd` to the directory and use relative filenames; multi-statement code → temp `.py` file run by name.
