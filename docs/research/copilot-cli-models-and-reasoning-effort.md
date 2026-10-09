# Copilot CLI models and reasoning effort

Values accepted by Copilot CLI as of 2026-10-09, as reported by the user's Copilot CLI. The list changes as providers release models; re-check before relying on it.

## Usage

```bash
copilot --model claude-sonnet-5.5 --reasoning-effort max
```

An unavailable `--model` fails at startup with exit code 1 ([copilot-cli-p-mode-permissions.md](../experiments/copilot-cli-p-mode-permissions.md)).

## Models (`--model`)

`claude-fable-5`, `claude-fable-5.1`, `claude-haiku-4.5`, `claude-haiku-5.5`, `claude-opus-4.8`, `claude-opus-5`, `claude-opus-5.5`, `claude-sonnet-5`, `claude-sonnet-5.5`, `gemini-3.7-flash`, `gemini-3.8-flash`, `gpt-5-mini`, `gpt-5.3-codex`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.5`, `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-6-astra`, `gpt-6-luna`, `gpt-6-sol`, `gpt-6.1-sol`, `grok-4.5`, `grok-4.6`, `grok-4.7`, `hydrafusion`, `mai-code-1.1-flash`

## Reasoning effort (`--reasoning-effort`)

Values: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`.

| Effort | Supported by |
|---|---|
| `xhigh` | claude-fable-5, claude-fable-5.1, claude-haiku-5.5, claude-opus-4.8, claude-opus-5, claude-opus-5.5, claude-sonnet-5, claude-sonnet-5.5, gpt-5.3-codex, gpt-5.4, gpt-5.4-mini, gpt-5.5, gpt-5.6-luna, gpt-5.6-sol, gpt-5.6-terra, gpt-6-astra, gpt-6-luna, gpt-6-sol, gpt-6.1-sol, grok-4.6, grok-4.7 |
| `max` | claude-fable-5, claude-fable-5.1, claude-haiku-5.5, claude-opus-4.8, claude-opus-5, claude-opus-5.5, claude-sonnet-5, claude-sonnet-5.5, gpt-5.6-luna, gpt-5.6-sol, gpt-5.6-terra, gpt-6-astra, gpt-6-luna, gpt-6-sol, gpt-6.1-sol |
