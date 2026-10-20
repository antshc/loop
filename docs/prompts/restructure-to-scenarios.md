# Prompt: restructure a project into scenario-driven (BDD-style) flows

Paste everything below the line into an agent chat (or save it as a `.prompt.md` in the target project). Replace the bracketed values.

---

Restructure `[TARGET PATH, e.g. src/app/]` into code-first, scenario-driven flows with live progress reporting. This is an architectural refactor: **behavior, public signatures, exit codes, side-effect order and error handling MUST stay identical.**

## Style

- Plain language code. No Gherkin, no feature files, no Given/When/Then wording in code, names or reports.
- Scenario and step names read as actions: "Process order #42", "Validate payment", "Publish draft pull request".
- Reporting only observes. It MUST NOT change outcomes, swallow exceptions, or log secrets, credentials or raw prompts.

## Layers

Dependency direction, import only downward: `entry point -> scenarios -> steps -> services -> infrastructure`.

| Layer | Owns | Must not |
|---|---|---|
| Entry point | CLI/config parsing, dependency wiring, reporter selection, top-level exit-code and error boundary | contain flow or rules |
| `scenarios/` | Control flow: branching, iteration, retries, nested scenario calls | call I/O, SDKs, subprocesses or storage directly |
| `steps/` | Small typed named actions, each wrapped in `@reported(...)`, delegating to services | hold rules or multi-action flow |
| `services/` | Business rules, validation, decisions, persistence sequencing | import `reporting`, `scenarios` or `steps` |
| Infrastructure / domain | External I/O (Git, HTTP, DB, files) and entities | know scenarios, steps or reporting |
| `reporting/` | `Reporter` contract, console reporter, optional Allure adapter | alter business results |

Prefer extracting existing functions over adding wrapper classes. Do not move domain or infrastructure modules just for naming. Introduce the minimum new modules.

## Procedure

1. **Baseline.** Run the existing tests and record the result. Identify entry points, current flows and their side-effect order. Stop and report if tests do not pass before you start.
2. **Reporting.** Add `reporting/` using the reference below. Default reporter is a no-op; the console reporter prints live indented progress with durations; color only on a TTY and never when `NO_COLOR` is set. Add `--quiet` to disable progress.
3. **Extract services.** Move rules and decisions out of the current flow functions into `services/`, unchanged.
4. **Add steps.** One thin `@reported` function per meaningful action. Pass collaborators explicitly.
5. **Write scenarios.** Re-express each top-level flow as a scenario calling steps and nested scenarios. Map returned failures and skips to `Status` with `status_of`; exceptions are reported as FAILED automatically and re-raised.
6. **Wire the entry point.** Keep its signature and injectable dependencies. Wrap the root scenario with `use_reporter(...)`.
7. **Enforce layers.** Add import-linter (or the language's equivalent) contracts: layered `entry point > scenarios > steps > services`; services and infrastructure forbidden from importing `reporting`, `scenarios`, `steps`.
8. **Tests.** Keep all existing behavior tests passing unchanged. Add reporting tests: nested hierarchy, returned FAILED/SKIPPED, exception reported then re-raised, broken reporter does not mask the result, no-reporter mode.
9. **Docs.** Add an `AGENTS.md` in the package describing each layer and the rules above, and a README section on how to run and read the progress output.
10. **Verify.** Run the full test suite and the layer checks. Run the real entry point (or `--help`) once and confirm the progress hierarchy matches the real call order.

## Optional Allure

Keep Allure optional (extra dependency, e.g. `reports`), lazily imported inside the adapter. Default runs MUST work without it: log a warning and continue when it is missing and was not requested explicitly; fail when the user passed an explicit results path. The root scenario is one test result; nested scenarios and steps are its steps. Verify generated results with an Allure viewer before relying on them.

## Reporting reference (Python)

```python
class Status(Enum): SUCCESS = "SUCCESS"; FAILED = "FAILED"; SKIPPED = "SKIPPED"

class Reporter(Protocol):
    def start(self, name: str, depth: int) -> None: ...
    def finish(self, name: str, depth: int, status: Status, elapsed: float) -> None: ...

_reporter: ContextVar[Reporter] = ContextVar("reporter", default=NullReporter())
_depth: ContextVar[int] = ContextVar("depth", default=0)

class Section:  # context manager: emits start, then finish with FAILED on exception else .status
    ...  # reporter errors are caught and logged, never raised

def reported(template, status_of=None):  # decorator: name = template.format(**bound call arguments)
    ...  # with Section(name) as s: result = fn(...); s.status = status_of(result) if status_of else s.status
```

Names use call arguments, e.g. `@reported("Deliver ticket #{ticket.number}")`. A complete implementation, console reporter and Allure adapter are in `workflows/dev_bdd/reporting/` of the Loop repository; copy and adapt them rather than rewriting.

## Done when

- Existing tests pass unchanged; new reporting and layer checks pass.
- Observable behavior (outputs, exit codes, side-effect order) is identical with reporting on, off, and with Allure absent.
- Each layer contains only what its row above allows.
- Report: files added/moved, any behavior risk you noticed, and anything you could not verify.
