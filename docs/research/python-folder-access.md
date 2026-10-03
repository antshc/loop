# Python tooling: folders read or written outside the project (Linux)

Scope: `python -m venv`, `pip install` (including `pip install -e ".[dev]"` with a setuptools `pyproject.toml` and build isolation), `python -m build`, `pytest`.

Sources: docs only (docs.python.org, pip.pypa.io, packaging.python.org, setuptools docs, docs.pytest.org). Exception: `python -m build` is documented only at build.pypa.io, a PyPA site outside the requested list. Rows that rely on it are tagged **[build]**. Citation tags such as `[pip-cache]` link to the URLs under **Sources**.

Tags: **[local]** = observed on this machine with read-only commands (Python 3.12.3, pip 26.1.1, uid 1000). **[inferred]** = follows from a cited doc but is not stated there. **[unverified]** = not confirmed by any doc read.

## Summary

- A venv-based flow writes in only four places outside the project: the pip cache, the temp dir, the venv itself, and (optionally) bytecode caches. All four can be relocated or disabled with env vars or flags.
- `venv` needs no network. `pip install -e ".[dev]"` and the isolated build of `python -m build` need an index (default `https://pypi.org/simple`). `pytest` itself does not.
- Everything that reads config or the user site is optional: `PIP_CONFIG_FILE=/dev/null` and `PYTHONNOUSERSITE=1` remove them.
- Outside a venv, pip refuses to install if `EXTERNALLY-MANAGED` exists in the stdlib dir. It exists on this machine **[local]**.

## Per-tool behaviour

1. **`python -m venv DIR`**
   - Creates `DIR` and parents, `pyvenv.cfg` with `home = <base python dir>`, `bin/` with a symlink or copy of the interpreter, and `lib/pythonX.Y/site-packages` ([venv]).
   - It then runs `ensurepip` unless `--without-pip` is given ([venv]). `ensurepip` "does not access the internet"; its wheels ship inside the stdlib ([ensurepip]).
   - `--upgrade-deps` is the only option that contacts PyPI ([venv]).
   - Since 3.13 it also writes a `.gitignore` in the venv ([venv]).
   - The venv interpreter reads `pyvenv.cfg` and uses `sys.base_prefix` at runtime, so the base Python dir must stay readable ([venv], [site]).
2. **`pip install -e ".[dev]"`**
   - Resolves, builds and installs in four stages ([pip-install]).
   - **Build isolation:** build requirements (`setuptools>=64`, `wheel`) are installed "in a temporary directory" added to `sys.path` ([pip-build]). Pip does not state which temp root it uses, but the default is `tempfile.gettempdir()`, which honours `TMPDIR` ([tempfile]) **[inferred]**.
   - **Editable build:** uses the PEP 660 `build_wheel_for_editable` hook. Editable wheels are not cached ([pip-build]).
   - **Cache:** HTTP responses and built wheels are cached. Wheels built from a local directory (`pip install .`) are not cached across runs ([pip-cache]).
   - **Install target:** setuptools adds a `.pth` file or import hook in `site-packages` ([st-dev]). Strict mode writes a link tree under `<project>/build/__editable__.*` ([st-dev]). Console scripts go in the venv `bin/` ([venv]).
   - **Bytecode:** `--compile` is the default, so pip byte-compiles installed files ([pip-install]).
3. **`python -m build`** **[build]**
   - Default flow: create a temporary venv, install `[build-system] requires` with pip, build an sdist, extract it to a random temp dir, build the wheel from that, write to `<srcdir>/dist/` ([build-how]).
   - Relocation:
     - `--outdir` sets the output directory.
     - `--env-dir` sets the isolated env location.
     - `--sdist-extract-dir` sets the extract location.
     - `TMPDIR` / `TEMP` / `TMP` set the temp base ([build-cli], [build-env]).
   - `--no-isolation` installs nothing and only checks that dependencies are present ([build-how]).
   - Build passes through `PIP_*`, `PYTHONPATH`, proxy and SSL variables to pip ([build-how]).
4. **`pytest`**
   - Writes the cache plugin dir `.pytest_cache`, default relative to rootdir ([pt-ref] `cache_dir`).
   - `tmp_path` goes under `{tempfile.gettempdir()}/pytest-of-{user}/pytest-{N}/`, keeping the last 3 runs by default ([pt-tmp]).
   - Python itself writes `__pycache__` for imported source modules ([py-cmd]).
   - Assertion rewriting writes `.pyc` files: pytest's `enable_assertion_pass_hook` doc says "clean the .pyc files … as assertions will require to be re-written" ([pt-ref]). Where it writes them is **[unverified]**.
   - No `~/.cache/pytest` is mentioned in the docs read. The default cache is under rootdir ([pt-cache], [pt-ref]), and `~/.cache` here has no `pytest` dir **[local]**.

## Path table

| Path | R/W | When | Relocation knob |
| --- | --- | --- | --- |
| Base Python: `/usr/bin/python3*`, `/usr/lib/python3.12` (stdlib, bundled ensurepip wheels) | R | Every run. Venv symlinks to it and reads `home` from `pyvenv.cfg` ([venv], [ensurepip]). | Not relocatable at runtime. `PYTHONHOME` overrides stdlib lookup ([py-cmd]). |
| `<stdlib>/EXTERNALLY-MANAGED` (`/usr/lib/python3.12/EXTERNALLY-MANAGED`) | R | `pip install` outside a venv. If present, pip must refuse unless `--break-system-packages` ([pep668], [pip-install]). | Run inside a venv. `PIP_BREAK_SYSTEM_PACKAGES` is the (unwanted) override. |
| New venv `DIR` (`pyvenv.cfg`, `bin/`, `lib/pythonX.Y/site-packages`, activate scripts) | W | `venv`; later `pip install` (packages, scripts, editable `.pth`) ([venv], [st-dev]) | `ENV_DIR` argument. `VIRTUAL_ENV` is only set by activation and is not needed ([venv]). |
| Venv `site-packages` | R, W | W by `pip install`. R by Python at startup and by pytest. | Choose the interpreter: `DIR/bin/python -m pip`. |
| `~/.cache/pip` (`http-v2`, `wheels`, `selfcheck`) | R, W | `pip install`. HTTP responses and built wheels ([pip-cache]). `selfcheck` is the version-check state **[local]**; its purpose is **[unverified]**. | `PIP_CACHE_DIR` / `--cache-dir`; `XDG_CACHE_HOME` ([pip-cache], [pip-gen]); `--no-cache-dir` / `PIP_NO_CACHE_DIR`. `~` follows `HOME` **[inferred]**. |
| pip config: `/etc/xdg/pip/pip.conf` (via `XDG_CONFIG_DIRS`), `/etc/pip.conf`, `~/.config/pip/pip.conf`, `~/.pip/pip.conf`, `$VIRTUAL_ENV/pip.conf` or interpreter root | R | Every pip run, in that load order; `PIP_CONFIG_FILE` loads last ([pip-conf]). None exist here **[local]**. | `PIP_CONFIG_FILE=/dev/null` disables all config files ([pip-conf]). `--isolated` ignores env vars and user config ([pip-gen]). `XDG_CONFIG_HOME` moves the user file. |
| Temp root `/tmp`, else `/var/tmp`, `/usr/tmp`, finally the cwd | W | Pip's build-isolation dir **[inferred]**. `build`'s isolated venv and sdist extract **[build]**. `pytest` `tmp_path` base ([pt-tmp]). | `TMPDIR` > `TEMP` > `TMP` ([tempfile]). Per tool: `--env-dir`, `--sdist-extract-dir` **[build]**; `--basetemp`, `PYTEST_DEBUG_TEMPROOT` ([pt-ref], [pt-tmp]). `PYTEST_DEBUG_TEMPROOT` is documented as the root for `tmp_path` dirs ([pt-ref]). |
| `<project>/**/__pycache__` | W | Importing source (tests, project). Pip also compiles at install time ([py-cmd], [pip-install]). | `PYTHONDONTWRITEBYTECODE=1` / `-B`; `PYTHONPYCACHEPREFIX=DIR` / `-X pycache_prefix=DIR` writes a mirror tree there instead ([py-cmd]); `pip --no-compile`. Whether `PYTHONPYCACHEPREFIX` applies to pip's install-time compile is **[unverified]**. |
| `<stdlib>` / `site-packages` `__pycache__` | R, maybe W | Importing stdlib or installed packages. Whether Python writes missing `.pyc` there is governed by the same switches ([py-cmd]); permission handling is **[unverified]**. | Same as above. |
| `*.egg-info` (here `tools/src/brain_tools.egg-info`) | W | Editable install or metadata generation in the source tree **[local]**; the setuptools docs read do not say where it is written **[unverified]**. Already gitignored (`.gitignore:356`) **[local]**. | None found in docs. Needs a writable source tree. |
| `<project>/build/` | W | Setuptools strict editable mode only: `build/__editable__.*` ([st-dev]). Default editable mode does not need it. `build/` is absent here **[local]**. | Avoid `editable_mode=strict` ([st-dev]). |
| `<project>/dist/` | W | `python -m build` default `--outdir` ([build-cli]) **[build]** | `--outdir PATH`. |
| `<project>/.pytest_cache` | R, W | Every `pytest` run ([pt-ref], [pt-cache]). Present here **[local]**. | `-o cache_dir=PATH` / ini `cache_dir` (env vars in value are expanded) ([pt-ref]); `-p no:cacheprovider` ([pt-cache]); `PYTEST_ADDOPTS` prepends options ([pt-ref]). |
| `~/.local/lib/python3.12/site-packages`, `~/.local/bin` (`USER_SITE`, `USER_BASE`) | R, W | R if user site is enabled and on `sys.path`. W only with `pip install --user` ([site], [pip-ug]). In a default venv it is off: `ENABLE_USER_SITE: False`, dir absent **[local]**. | `PYTHONUSERBASE` ([py-cmd], [pip-ug]); `PYTHONNOUSERSITE=1` / `-s`; `-I` ignores `PYTHON*` vars ([py-cmd]). |
| Python startup files: `sitecustomize`, `usercustomize` in site dirs | R | Python startup ([site]) | `-S`, `-s`, `-I` ([py-cmd]). |
| Network: `https://pypi.org/simple` and the file URLs it links to | Net | pip dependency resolution and downloads ([pip-install]) | See **Network** below. |

## Network

| Step | Needs network? | Why / how to avoid |
| --- | --- | --- |
| `python -m venv` | No by default | `ensurepip` is offline ([ensurepip]). `--upgrade-deps` goes to PyPI ([venv]). |
| `pip install -e ".[dev]"` | Yes | Runtime deps (`python-json-logger`), the `dev` extra (`pytest` and its transitive deps), and build requirements (`setuptools>=64`, `wheel`) come from the index ([pip-install], [pip-build]). Offline: `--no-index --find-links=DIR` ([pip-install], [pip-ug]) plus pre-downloaded wheels, and `--no-build-isolation` if setuptools and wheel are already installed ([pip-build]). |
| pip version check | Yes, periodic | `--disable-pip-version-check` / `PIP_DISABLE_PIP_VERSION_CHECK` stops it ([pip-gen]). |
| `python -m build` | Yes (isolated), no with `--no-isolation` | Installs build requirements into the temp venv via pip **[build]**. The `build` package itself must already be installed. |
| `pytest` | No | The docs read describe no network access in the core run. Tests may open their own. |

Index and proxy knobs: `--index-url` / `PIP_INDEX_URL`, `--extra-index-url`, `--no-index`, `--find-links` ([pip-install]); `--proxy`, `http_proxy`, `https_proxy`, `no_proxy` ([pip-ug]); `PIP_CERT`, `--trusted-host` ([pip-gen]). The host serving the actual files for PyPI (`files.pythonhosted.org`) is **[unverified]**: the docs read say only "the file URLs linked from the index".

## Knobs

| Knob | Effect | Source |
| --- | --- | --- |
| `PIP_CACHE_DIR`, `--cache-dir` | Move the pip cache | [pip-gen] |
| `PIP_NO_CACHE_DIR`, `--no-cache-dir` | Disable the pip cache (docs advise against unless a higher-level cache exists) | [pip-cache], [pip-gen] |
| `XDG_CACHE_HOME` | Linux pip cache becomes `$XDG_CACHE_HOME/pip` | [pip-cache] |
| `PIP_CONFIG_FILE` | Extra config file loaded last; `os.devnull` disables all config files | [pip-conf] |
| `XDG_CONFIG_HOME`, `XDG_CONFIG_DIRS` | Move user / global pip config locations | [pip-conf] |
| `--isolated`, `PIP_ISOLATED` | Ignore env vars and user config | [pip-gen] |
| `PIP_NO_INPUT`, `--no-input` | Disable prompts | [pip-gen] |
| `PIP_REQUIRE_VIRTUALENV` | Refuse to run outside a venv | [pip-gen] |
| `PIP_DISABLE_PIP_VERSION_CHECK` | No PyPI version check | [pip-gen] |
| `--no-build-isolation`, `PIP_NO_BUILD_ISOLATION` | No temp build env; build requirements must already be installed | [pip-install], [pip-build] |
| `--no-compile`, `PIP_NO_COMPILE` | Skip install-time bytecode | [pip-install] |
| `--target`, `--prefix`, `--root`, `--user` | Change the install destination | [pip-install] |
| `PYTHONUSERBASE` | Moves `USER_BASE` and `USER_SITE` | [site], [py-cmd] |
| `PYTHONNOUSERSITE`, `-s` | Drop user site from `sys.path` | [py-cmd] |
| `PYTHONDONTWRITEBYTECODE`, `-B` | No `.pyc` writes | [py-cmd] |
| `PYTHONPYCACHEPREFIX`, `-X pycache_prefix` | `.pyc` goes to a mirror tree at the given path | [py-cmd] |
| `TMPDIR`, `TEMP`, `TMP` | Temp root for Python's `tempfile` and `build` | [tempfile], [build-env] |
| `HOME` | Base for `~` (`~/.cache`, `~/.config`, `~/.local`, `~/.pip`) **[inferred]**. pip documents `$HOME/.config/pip/pip.conf` and `$HOME/.pip/pip.conf` | [pip-conf] |
| `VIRTUAL_ENV` | Set by activation (and by `build` for its isolated env); not required to use a venv. `$VIRTUAL_ENV/pip.conf` is pip's site config | [venv], [pip-conf], [build-env] |
| `--env-dir`, `--sdist-extract-dir`, `--outdir` | `build` locations **[build]** | [build-cli] |
| `PYTEST_ADDOPTS` | Options prepended to the command line | [pt-ref] |
| `-o cache_dir=PATH` | `.pytest_cache` location | [pt-ref] |
| `-p no:cacheprovider` | Disable pytest's cache plugin | [pt-cache] |
| `--basetemp=DIR` | `tmp_path` base; "removed if it exists" | [pt-ref], [pt-tmp] |
| `PYTEST_DEBUG_TEMPROOT` | Root for `tmp_path` dirs | [pt-ref], [pt-tmp] |

## Minimal set a sandbox must allow

Create the venv, install the project with dependencies, run tests:

1. **Read (and exec) the base Python:** `/usr/bin/python3*` and `/usr/lib/python3.12` (stdlib, `venv`, bundled `ensurepip` wheels). The venv interpreter reads `home` from `pyvenv.cfg` at every start.
2. **Read and write the project dir.** This covers the venv (e.g. `.venv/`), `__pycache__`, `.pytest_cache`, `dist/`, and `*.egg-info` in the source tree **[local]**.
3. **Read and write a temp dir** (`/tmp` or `TMPDIR`) for pip's build isolation **[inferred]** and `tmp_path`.
4. **Read and write a pip cache dir** (`~/.cache/pip` or `PIP_CACHE_DIR`), or pass `--no-cache-dir` and drop this path.
5. **Network egress to the package index** (`pypi.org:443`, plus the file host **[unverified]**), plus DNS and CA certificates (paths **[unverified]**), unless installing offline from a local wheel directory.
6. **Optional reads:** pip config paths. Absent here, so not required **[local]**.

Not needed: `~/.local`, system `dist-packages`, `~/.cache/pytest`, any write to `/usr`. Run pip as `DIR/bin/python -m pip` (not the system `pip`) so `EXTERNALLY-MANAGED` is not hit ([pep668]).

## Variant: everything under one project folder

Allow only the project folder (plus read-only base Python and network egress). With `PROJ` as the project root:

```bash
S="$PROJ/.sandbox"
export HOME="$S/home"                       # catches ~ expansions (inferred)
export XDG_CACHE_HOME="$S/cache"
export XDG_CONFIG_HOME="$S/config"
export PIP_CACHE_DIR="$S/cache/pip"
export PIP_CONFIG_FILE=/dev/null            # disable all pip config files
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_NO_INPUT=1
export TMPDIR="$S/tmp"                      # pip build isolation, tmp_path, build
export PYTHONUSERBASE="$S/userbase"
export PYTHONNOUSERSITE=1
export PYTHONPYCACHEPREFIX="$S/pycache"     # or PYTHONDONTWRITEBYTECODE=1
export PYTEST_ADDOPTS="-o cache_dir=$S/pytest_cache"
mkdir -p "$S"/{home,cache/pip,config,tmp,userbase,pycache}

python3 -m venv "$PROJ/.venv"
"$PROJ/.venv/bin/python" -m pip install -e ".[dev]"
"$PROJ/.venv/bin/python" -m pytest
```

- The `mkdir` is a precaution: the docs read do not say whether each tool creates a missing `HOME`, `XDG_*` or `TMPDIR` directory **[unverified]**. `PYTEST_ADDOPTS` and `cache_dir` are cited in [pt-ref].
- For `python -m build`, add `--outdir "$PROJ/dist"`. Optionally `--env-dir "$S/buildenv"` (must be empty) and `--sdist-extract-dir "$S/sdist"` **[build]**.
- `PYTHONPYCACHEPREFIX` puts `.pyc` files outside `__pycache__` dirs, but still inside `$S`. Its effect on pip's install-time compile is **[unverified]**; add `pip install --no-compile` to avoid depending on it.
- The only paths still read outside `$PROJ` are the base Python and network files (DNS, certs).

## Local observations

- `python` here is `/home/pet/.venvs/global/bin/python` (`home = /usr/bin`, `include-system-site-packages = false`). Its `sys.path` includes `/home/pet/_projects/afk/brain/tools/src` through an editable install **[local]**. Running `python -m venv` from this interpreter reads that venv. The venv doc says creating a venv from a venv uses the underlying base `executable` ([venv]); whether the new venv inherits that path is **[unverified]**. Use `/usr/bin/python3` to avoid this.
- `/etc/pip.conf`, `/etc/xdg/pip/pip.conf`, `~/.config/pip/pip.conf`, `~/.pip/pip.conf` and `<venv>/pip.conf` are all absent **[local]**.
- Default pip cache `/home/pet/.cache/pip` exists with `http-v2`, `selfcheck`, `wheels` **[local]**.
- `/usr/lib/python3.12/EXTERNALLY-MANAGED` exists **[local]**.
- The repo has no `README.md`, but `pyproject.toml` sets `readme = "README.md"` **[local]**. Behaviour of the editable install without it was not tested here **[unverified]**, so verify before relying on the commands above.

## Not verified

- Which temp root pip uses for its build-isolation dir (assumed `tempfile.gettempdir()`).
- Where `*.egg-info` is written and whether any knob moves it.
- Whether pytest's assertion-rewrite `.pyc` writes honour `PYTHONPYCACHEPREFIX` / `PYTHONDONTWRITEBYTECODE`.
- Pip's `selfcheck` file purpose; the CA-certificate and DNS paths; the PyPI file host.
- Whether `PYTHONPYCACHEPREFIX` is honoured by pip's install-time compile.
- Pip docs read are v26.2.1; local pip is 26.1.1. The 26.2 note in the config and cache docs concerns macOS only ([pip-conf], [pip-cache]).

## Answer

- **Outside the project, a venv flow touches:** the base Python (read-only), the pip cache, the temp dir, and optionally pip config, the user site, and bytecode caches. Everything else (venv, `egg-info`, `__pycache__`, `.pytest_cache`, `dist/`) is in or under the project.
- **Network:** only `pip install` (deps, `setuptools`/`wheel`, version check) and the isolated `python -m build` need it. `venv` and `pytest` do not.
- **Minimum sandbox allow-list:** read base Python; read/write the project, one temp dir and one pip cache dir (or `--no-cache-dir`); egress to the package index.
- **One-folder variant:** set `HOME`, `XDG_*`, `PIP_CACHE_DIR`, `PIP_CONFIG_FILE=/dev/null`, `TMPDIR`, `PYTHONUSERBASE`, `PYTHONNOUSERSITE`, `PYTHONPYCACHEPREFIX` and `PYTEST_ADDOPTS="-o cache_dir=…"` under `$PROJ/.sandbox`. Details are in the script above.

## Sources

[venv]: https://docs.python.org/3/library/venv.html
[site]: https://docs.python.org/3/library/site.html
[py-cmd]: https://docs.python.org/3/using/cmdline.html
[tempfile]: https://docs.python.org/3/library/tempfile.html
[ensurepip]: https://docs.python.org/3/library/ensurepip.html
[pep668]: https://packaging.python.org/en/latest/specifications/externally-managed-environments/
[pip-cache]: https://pip.pypa.io/en/stable/topics/caching/
[pip-conf]: https://pip.pypa.io/en/stable/topics/configuration/
[pip-install]: https://pip.pypa.io/en/stable/cli/pip_install/
[pip-gen]: https://pip.pypa.io/en/stable/cli/pip/
[pip-build]: https://pip.pypa.io/en/stable/reference/build-system/
[pip-ug]: https://pip.pypa.io/en/stable/user_guide/
[st-dev]: https://setuptools.pypa.io/en/latest/userguide/development_mode.html
[pt-ref]: https://docs.pytest.org/en/stable/reference/reference.html
[pt-cache]: https://docs.pytest.org/en/stable/how-to/cache.html
[pt-tmp]: https://docs.pytest.org/en/stable/how-to/tmp_path.html
[build-how]: https://build.pypa.io/en/stable/explanation/how-it-works.html
[build-cli]: https://build.pypa.io/en/stable/reference/cli.html
[build-env]: https://build.pypa.io/en/stable/reference/environment-variables.html
