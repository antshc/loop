# Rich as a Python alternative to `@clack/prompts`

Scope: terminal output and prompts for a Python CLI, mapped to `@clack/prompts` features (intro/outro, note, spinner, log, select/confirm/text).

Tags: **[unverified]** = from general knowledge, not checked against docs or a local install in this session.

## Summary

- Rich is the closest Python equivalent for clack's styled output. Pair it with questionary for rich prompts.
- Rich ships a basic `rich.prompt` (`Prompt`, `Confirm`, `IntPrompt`, `FloatPrompt`) **[unverified]**: text, yes/no and choice-validated input only. It has no arrow-key select or multiselect.
- Textual is a full TUI framework by the Rich authors. It is far heavier than clack and needed only for app-style UIs.

## clack to Python mapping

| clack | Python | Notes |
|---|---|---|
| `intro` / `outro` | `rich.console.Console.print` with styles or `rich.rule.Rule` | No built-in framing. Compose manually. |
| `note` | `rich.panel.Panel` | Direct equivalent. |
| `spinner` | `Console.status(...)` (`rich.status.Status`) | Context manager. Update with `status.update(...)`. |
| `log.info/warn/error` | `Console.print` with styles, or `Console.log` | `Console.log` adds timestamp and caller. |
| streaming/live area | `rich.live.Live` | Re-renders a renderable in place. |
| tables, progress | `rich.table.Table`, `rich.progress.Progress` | Beyond clack. |
| `text` / `confirm` | `rich.prompt.Prompt.ask` / `Confirm.ask` | Basic only. |
| `select` / `multiselect` | `questionary.select` / `checkbox` | Built on prompt_toolkit. |

## Other options

- **Prompts:** questionary (select/confirm/text), InquirerPy (Inquirer.js-style), prompt_toolkit (low-level base of both), Typer (`typer.prompt`/`confirm`, Rich output built in).
- **Spinners/progress only:** halo, yaspin, tqdm, alive-progress.
- **CLI framework with Rich styling:** Typer, rich-click.
- **`clack-py`:** small clack port, less maintained than Rich **[unverified]**.

## Recommendation

Rich for output (note, spinner, log) plus questionary for interactive prompts. Both are pure Python and work in non-interactive runs when stdout is not a TTY: Rich degrades to plain text, whereas prompts need a TTY **[unverified]**.
