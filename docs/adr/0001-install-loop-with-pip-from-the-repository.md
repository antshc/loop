# Install Loop with pip from the repository

Loop must reach users in a form whose version the project controls. It is installed with `pip` from this repository (`pip install -e ".[dev]"` for development), so the repository revision defines the delivered version.

## Considered Options

- **Run scripts directly from the repository without installing** — rejected: gives no controlled version and no straightforward delivery to the user.
- **Standalone binary, `pipx`, or zipapp distribution** — rejected: version control and delivery to the user are easier when installing from the repository with `pip`.

## Consequences

- Users need Python and `pip` and must update by pulling the repository and reinstalling.
- No package index is involved; the repository is the only distribution channel.
- The package name is set by [ADR 0002](0002-ship-loop-as-the-loop-package-with-an-loop-command.md); Loop ships no command.
