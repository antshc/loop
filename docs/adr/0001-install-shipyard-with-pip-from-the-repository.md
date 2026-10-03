# Install Shipyard with pip from the repository

Shipyard (`brain-tools`, exposing `afk_dev`, `afk_fix_prs`, `afk_address_prs`) must reach users in a form whose version the project controls. It is installed with `pip` from this repository (`pip install -e ".[dev]"` for development), so the repository revision defines the delivered version.

## Considered Options

- **Run scripts directly from the repository without installing** — rejected: gives no controlled version and no straightforward delivery to the user.
- **Standalone binary, `pipx`, or zipapp distribution** — rejected: version control and delivery to the user are easier when installing from the repository with `pip`.

## Consequences

- Users need Python and `pip` and must update by pulling the repository and reinstalling.
- No package index is involved; the repository is the only distribution channel.
- Package and command names are set by [ADR 0002](0002-ship-shipyard-as-the-shipyard-package-with-a-ship-command.md).
