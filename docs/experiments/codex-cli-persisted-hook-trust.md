# Experiment: Codex persisted hook trust vs per-run env changes

Answers "Further Considerations" item 6 of [agent-cli-hooks-prototype.md](../plans/agent-cli-hooks-prototype.md). Run 2026-10-10.

## Question

Does a persisted Codex hook trust (`hooks.state` in the user `config.toml`) keep a `-c hooks.<Event>=[…]` hook running in `codex exec` without `--dangerously-bypass-hook-trust` when per-run state changes: env vars, working directory, `CODEX_HOME`?

Out of scope: project-layer (`.codex/`) hooks, `exec resume`, the interactive `/hooks` flow, Docker.

## Method

- **CLI under test:** `codex-cli 0.162.1`, model `gpt-5.5`.
- **Scratch:** `/tmp/loop-codex-trust-exp`, with `CODEX_HOME` pointing at a scratch `home/` that holds only a copy of `auth.json`. The real `~/.codex` was not written.
- **Hook:** one `SessionStart` override, `-c 'hooks.SessionStart=[{matcher="startup|resume",hooks=[{type="command",command=<hook.sh>,timeout=5}]}]'`. `hook.sh` appends `$LOOP_MARK` and the payload to `hook.log`.
- **Getting the key and hash:** `codex app-server` over stdio, JSON-RPC `hooks/list` with the same `-c` override. It returned `key` `/<session-flags>/config.toml:session_start:0:0`, `currentHash` `sha256:5b46…ffd` and `trustStatus` `untrusted`.
- **Persisting trust:** wrote that key and hash to the scratch `config.toml`:

  ```toml
  [hooks.state."/<session-flags>/config.toml:session_start:0:0"]
  trusted_hash = "sha256:5b469ebb19c5356f5118c3865f19c29a3856f8c1345fce87e50b89e10e731ffd"
  ```

  `hooks/list` then reported `trusted`.
- **Runs:** `codex exec --skip-git-repo-check --ephemeral -m gpt-5.5 -c <override> 'Reply with the single word OK'`, no bypass flag. "Fired" means `hook.log` was written and stderr had `hook:` lines.

## Observations

| # | Change from the trusted baseline | Fired |
|---|---|---|
| 1 | None (`LOOP_MARK=a`) | yes |
| 2 | Env changed: `LOOP_MARK=b`, plus new `EXTRA_VAR`, `OPENAI_LOG` | yes |
| 3 | Different working directory (second `git init` dir), same `CODEX_HOME` | yes |
| 4 | Hook `timeout` edited 5 to 6 | no (`hooks/list`: `modified`, new hash `sha256:5468…d45`) |
| 5 | Fresh `CODEX_HOME`, no `hooks.state` | no |
| 6 | Same as 5 (repeat, different env) | no |
| 7 | Fresh `CODEX_HOME` with the `config.toml` `hooks.state` copied in | yes |

- Every run exited 0 and printed `OK`. The untrusted runs (4, 5, 6) gave no warning or error, only the absence of hooks.

## Conclusion

- **Yes for env vars and working directory:** persisted trust survives per-run env changes and a different cwd. The hash covers the hook definition (command, matcher, timeout, event), not the environment, and `/<session-flags>/config.toml:<event>:<i>:<j>` keys contain no worktree path.
- **No for `CODEX_HOME`:** trust lives in the active home's `config.toml`. A new home sees the hook as untrusted and skips it silently, unless `hooks.state` is copied in (case 7).
- **No for definition changes:** any edit to the command, matcher or timeout gives `modified` and the hook is skipped silently. The shim command embeds `--session <SessionName>` and the shim path, so every distinct session name is a new hash.
- Implication for the plan: with trust persisted per hash, a per-session shim command would need trust written for every run. `CodexCli(hook_trust_bypass=True)` stays the practical route. Persisting trust would be viable only if the shim command is constant (session name passed through something outside the hashed definition, which Codex does not offer: no `CODEX_*` env reaches the hook) or if the runner writes `hooks.state` into the active home before each run.

## Evidence

- `hooks/list` before and after writing `hooks.state`: `untrusted` to `trusted`; after the timeout edit: `modified`.
- `hook.log` present for 1, 2, 3, 7; absent for 4, 5, 6. stderr `hook:` lines match.
- `codex exec --help`: `--dangerously-bypass-hook-trust` is "Run enabled hooks without requiring persisted hook trust for this invocation".

## Cleanup

`/tmp/loop-codex-trust-exp` removed. `~/.codex` untouched. No repo files other than this note and the plan edit changed.
