# .NET CLI: folders and network used by `dotnet restore` / `build` / `run` on Linux

Sources: Microsoft Learn plus `dotnet/sdk`, `dotnet/msbuild`, `dotnet/roslyn`, `dotnet/runtime`, `NuGet/NuGet.Client` source on their default branches (docs reading only, nothing was executed). Numbers like [S3] point to the Sources list at the end. Claims that no fetched source confirms are marked **(unverified)**.

## Summary

- Outside the project, the CLI touches these locations: the NuGet global-packages folder, an HTTP cache, a plugins cache, a NuGet scratch dir, NuGet config files, `~/.dotnet`, the SDK/runtime install dir, `/tmp`, and (on `run`) the .NET X.509 store, which holds the ASP.NET dev cert [S1][S3][S5].
- Two knobs relocate most of it. `HOME` moves everything derived from the user profile. `DOTNET_CLI_HOME` moves the SDK's `~/.dotnet` and, in NuGet running on .NET Core, the "home" that `~/.nuget` and `~/.local/share` derive from [S3][S9][S10].
- `TMPDIR` moves only NuGet's scratch dir. MSBuild node pipes and Roslyn compiler-server pipes are hard-coded to `/tmp` on Unix, so `TMPDIR` does not move them [S1][S11][S12].
- Network is needed only for NuGet restore of packages not already in the global-packages folder. Two things also make network calls without being a restore of missing packages: NuGet Audit and SDK telemetry. The workload-manifest download is a third background network call [S1][S6][S7][S13][S14].
- `--disable-build-servers` forwards exactly `UseRazorBuildServer=false`, `UseSharedCompilation=false` and `/nodeReuse:false`. It removes the persistent processes, not the per-build sockets in `/tmp` **(inferred, see Build servers)** [S8].

## Path table

"Default" is the Linux default. "Knob" is what relocates or disables it.

| Path (default) | R/W | When | Knob |
| --- | --- | --- | --- |
| `~/.nuget/packages` (global-packages) | rw | restore writes downloaded packages. build/run read them via `project.assets.json` | `NUGET_PACKAGES` (wins over config), `globalPackagesFolder` in nuget.config, `RestorePackagesPath` MSBuild property, `dotnet restore --packages` [S1][S4] |
| `~/.local/share/NuGet/v3-cache` (http-cache) | rw | restore, for feed responses (about 30 min expiry) | `NUGET_HTTP_CACHE_PATH`, `--no-http-cache` [S1]. NuGet source on `dev` names the dir `http-cache`, so verify with `dotnet nuget locals all --list` [S9] |
| `~/.local/share/NuGet/plugins-cache` | rw | restore with credential/auth plugins (operation-claims cache) | `NUGET_PLUGINS_CACHE_PATH` [S1]. Source on `dev` says `plugin-cache` [S9] |
| `/tmp/NuGetScratch<user>` (temp) | rw | restore and package install. Used for file locks that coordinate access to http-cache and global-packages | `NUGET_SCRATCH`. Otherwise `Path.GetTempPath()`, which honors `TMPDIR`, plus the username on Linux [S1][S9] |
| `$XDG_DATA_HOME` or `~/.local/share` | rw | parent of both NuGet caches above | `XDG_DATA_HOME` (read by NuGet, else `<home>/.local/share`) [S9] |
| `~/.nuget/NuGet/NuGet.Config` (user config, .NET CLI) | r (may be created when missing: **unverified**) | every restore | `dotnet restore --configfile <file>` uses only that file [S2][S4]. Location follows the NuGet "home" (see `DOTNET_CLI_HOME`/`HOME` row) [S9] |
| `~/.nuget/config/*.config` (additional user configs) | r | every restore | same home dependence [S2] |
| `/etc/opt/NuGet/Config/` (machine-wide) | r | every restore | `NUGET_COMMON_APPLICATION_DATA` (replaces `/etc/opt`) [S2][S9] |
| `$XDG_DATA_HOME/NuGetDefaults.config` | r | every restore (rare) | `XDG_DATA_HOME` [S2] |
| Each folder from `/` down to the project (`nuget.config`) | r | every restore | `--configfile` [S2] |
| `<project>/obj/` (`project.assets.json`, nuget caches) | rw | restore, build | `--artifacts-path`, `BaseIntermediateOutputPath` [S4][S5] |
| `<project>/bin/` | rw | build, run | `-o/--output`, `--artifacts-path` [S5] |
| `~/.dotnet/<ver>.dotnetFirstUseSentinel`, `<ver>.aspNetCertificateSentinel`, `<ver>_<key>.dotnetUserLevelCache` | rw | first `dotnet` CLI command (a first-run action). Also runs NuGet state migrations | `DOTNET_CLI_HOME` (moves `~/.dotnet`). `DOTNET_NOLOGO=1` only hides the message. `DOTNET_SKIP_FIRST_TIME_EXPERIENCE` has no effect on SDK 3.0+ [S3][S10][S15][S16] |
| `~/.dotnet/` (tools, workload packs/manifests, SDK-release metadata cache) | rw | local/global tools. Background workload-manifest download on restore/build. Opt-in SDK vulnerability check | `DOTNET_CLI_HOME`, `DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE`, `DOTNET_SDK_VULNERABILITY_CHECK_DISABLE` [S3][S6][S17] |
| `~/.dotnet/TelemetryStorageService` | rw | telemetry spool | **(unverified)** path. `DOTNET_CLI_TELEMETRY_OPTOUT=1` disables telemetry [S7] |
| `/usr/share/dotnet` or `/usr/lib/dotnet` or `~/.dotnet` (SDK + runtime install, packs, `sdk/`, `shared/`, `packs/`) | r | all commands | `DOTNET_ROOT` (apphost only). SDK resolver overrides `DOTNET_MSBUILD_SDK_RESOLVER_*` [S3] |
| `/tmp/MSBuild<pid>` (Unix domain sockets) | rw | build with MSBuild worker nodes / node reuse | **Not movable via `TMPDIR`** (hard-coded `/tmp`). `MSBUILDDISABLENODEREUSE=1`, `-nr:false` [S11][S18][S19] |
| `/tmp/<pipe name>` (VBCSCompiler server socket) | rw | C# compile via compiler server | **Not movable via `TMPDIR`** (hard-coded `/tmp`). `UseSharedCompilation=false` [S12][S8] |
| `/tmp/.dotnet/...` (runtime named-mutex files used by the compiler server) | rw | when the compiler server starts | **(unverified)** path. `UseSharedCompilation=false` avoids the server [S12] |
| Razor build-server pipe | rw | Razor/Blazor code generation | `UseRazorBuildServer=false` (defaults to `UseSharedCompilation`). Pipe location **(unverified)** [S8][S20] |
| `~/.dotnet/corefx/cryptography/x509stores/my` (X.509 `CurrentUser\My`) | rw | first-run ASP.NET dev-cert generation. `dotnet run` of an HTTPS Kestrel app reads the dev cert from here | `HOME` (the runtime reads `$HOME`, **not** `DOTNET_CLI_HOME`). `DOTNET_GENERATE_ASPNET_CERTIFICATE=false` stops generation [S3][S21][S22][S23] |
| `~/.aspnet/dev-certs/trust` | rw | only on `dotnet dev-certs https --trust` | `HOME`. Not used by restore/build/run [S24] |
| `~/.aspnet/DataProtection-Keys` | rw | `run`, only if the app uses Data Protection (auth cookies, antiforgery) | `HOME`, or `PersistKeysToFileSystem(...)` in app code [S25] |
| `~/.microsoft/usersecrets/<UserSecretsId>/secrets.json` | r | `run`, only in the `Development` environment with a `UserSecretsId` | `HOME` (**unverified**). Remove `UserSecretsId` [S26] |
| `/etc/ssl/certs` (OpenSSL trust roots) | r | TLS to nuget.org during restore | `SSL_CERT_FILE`, `SSL_CERT_DIR` [S27] |

Notes on specific rows:

- **Home relocation.** In NuGet running on .NET Core, "home" is `DOTNET_CLI_HOME`, falling back to the user profile (`HOME` on Unix) [S9]. The SDK computes `~/.dotnet` the same way: `DOTNET_CLI_HOME`, then `HOME`, then the OS passwd entry [S10]. So `DOTNET_CLI_HOME` moves `~/.nuget/packages`, `~/.nuget/NuGet`, `~/.local/share/NuGet` (unless `XDG_DATA_HOME` is set) and `~/.dotnet`. It does not move `$HOME`-based paths read by the .NET runtime or ASP.NET: the X.509 store and `~/.aspnet` [S21][S25].
- **Doc/code mismatch.** The Learn page lists `v3-cache` and `plugins-cache` for Linux [S1]. The `NuGet.Client` `dev` source returns `http-cache` and `plugin-cache` [S9]. It is unknown which names a given installed SDK uses. Treat the parent folder `~/.local/share/NuGet` as the stable unit.
- **Env vars from the question that do not matter on Linux/.NET 6+.**
  - `DOTNET_SKIP_FIRST_TIME_EXPERIENCE` has no effect on .NET Core 3.0+. Use `DOTNET_NOLOGO` for messages [S3].
  - `DOTNET_NOLOGO` changes only the welcome and telemetry text, not telemetry itself or the sentinel files [S3].
- **`dotnet run` and MSBuild switches.** `dotnet run` builds implicitly, but MSBuild switches such as `-nr:false` are not forwarded by `dotnet run`, and `-p:` arguments are not respected for its implicit build [S5][S28]. Use the environment variables or a `Directory.Build.props` for `run`.

## Network access

| Destination | Trigger | Disable or avoid |
| --- | --- | --- |
| nuget.org (`https://api.nuget.org/v3/index.json`) or whatever `packageSources` lists | Restore, for any package/version not in global-packages and not in a non-HTTP source. Lookup order: global-packages, non-HTTP sources, http-cache, then HTTP [S1][S2][S29] | Pre-populate global-packages, use `--no-restore` on build/run, point `--source`/`--configfile` at a local folder feed [S1][S4][S5] |
| Package CDN hosts behind the service index | Actual `.nupkg` download | Host names are not given in the fetched sources **(unverified)**. Allow whatever the service index returns |
| Audit sources (default: `packageSources`; may also be `api.nuget.org` or `data.nuget.org`) | NuGet Audit during restore [S13] | `NuGetAudit=false` MSBuild property (**unverified** in the fetched text), or `auditSources` in nuget.config [S13] |
| Microsoft telemetry endpoint | Any CLI command, including `build`/`run` (not `dotnet app.dll`) [S7] | `DOTNET_CLI_TELEMETRY_OPTOUT=1` [S7] |
| Workload advertising manifests | Background download started by `restore`/`build`; stopped if still running at exit [S4][S5] | `DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE=true` [S3][S6] |
| SDK release metadata (vulnerability/EOL check) | Opt-in check (`CheckSdkVulnerabilities`), background refresh [S3] | `DOTNET_SDK_VULNERABILITY_CHECK_DISABLE=true` [S3] |
| Loopback listen (Kestrel, e.g. ports from `launchSettings.json`) | `dotnet run` | **(unverified)** in the fetched sources. Needs `bind` on 127.0.0.1 |

Nothing in `build` needs network once restore has succeeded, except the telemetry and background items above.

## Build servers

- `--disable-build-servers` (SDK 7+ on `build`/`restore`) is implemented as `--property:UseRazorBuildServer=false --property:UseSharedCompilation=false /nodeReuse:false` [S8][S4][S5]. It "forces the command to ignore any persistent build servers" [S4].
- `dotnet build-server shutdown [--msbuild] [--razor] [--vbcscompiler]` stops the running servers [S30].
- MSBuild Server (opt-in) is disabled by `DOTNET_CLI_USE_MSBUILD_SERVER=false` or `/nr:false` [S19]. Node reuse is on by default [S18]. `MSBUILDDISABLENODEREUSE=1` is the environment-variable form of `/nr:false` [S31].
- **Inference (unverified):** with node reuse off, a parallel build (`-m`, the CLI default) can still create short-lived worker-node sockets under `/tmp` for the duration of the build, because the pipe path is built from the literal `/tmp` [S11]. `-m:1` keeps work in one process. Check before relying on it.

## Minimal allow-list: restore + build + run an ASP.NET project (default env)

Write:

1. Project dir (`obj/`, `bin/`).
2. `~/.nuget/packages`.
3. `~/.local/share/NuGet`.
4. `/tmp` (NuGetScratch, MSBuild and Roslyn sockets, runtime temp).
5. `~/.dotnet` (sentinels, tools, dev-cert store at `~/.dotnet/corefx/...`).

Read:

6. SDK/runtime install dir (`/usr/share/dotnet`, `/usr/lib/dotnet`, or `~/.dotnet`).
7. `~/.nuget/NuGet/NuGet.Config` and `~/.nuget/config/`.
8. `/etc/opt/NuGet/Config`.
9. `/etc/ssl/certs`.
10. All parent dirs of the project (for `nuget.config`, `Directory.Build.props`, `global.json`).

Conditional writes: `~/.aspnet/DataProtection-Keys` (app uses Data Protection), `~/.microsoft/usersecrets` (read-only for `run` in Development).

Network:

- Outbound HTTPS to the feed host(s) in `packageSources` and audit sources, plus package download hosts.
- Telemetry endpoint, unless opted out.
- Loopback listen for `run`.
- If packages are pre-populated and audit and telemetry are off, no outbound network is needed.

## Variant: everything under one project folder

Set these before running `dotnet`. `$P` is a folder inside the project (add it to `.gitignore`).

```bash
P="$PWD/.sandbox"; mkdir -p "$P"/{home,cli,nuget,xdg,tmp}

# relocate user-profile paths (x509 store, ~/.aspnet, ~/.microsoft, passwd-less HOME)
export HOME="$P/home"
export DOTNET_CLI_HOME="$P/cli"            # ~/.dotnet, workloads, tools, NuGet "home"
export XDG_DATA_HOME="$P/xdg"              # NuGet http-cache + plugins cache parent
export NUGET_PACKAGES="$P/nuget/packages"
export NUGET_HTTP_CACHE_PATH="$P/nuget/http-cache"
export NUGET_PLUGINS_CACHE_PATH="$P/nuget/plugins-cache"
export NUGET_SCRATCH="$P/nuget/scratch"
export TMPDIR="$P/tmp"                     # .NET temp files (not MSBuild/Roslyn pipes)

# silence/disable side effects
export DOTNET_NOLOGO=1 DOTNET_CLI_TELEMETRY_OPTOUT=1
export DOTNET_GENERATE_ASPNET_CERTIFICATE=false
export DOTNET_ADD_GLOBAL_TOOLS_TO_PATH=false
export DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE=true
export DOTNET_SDK_VULNERABILITY_CHECK_DISABLE=true

# no persistent servers
export MSBUILDDISABLENODEREUSE=1 DOTNET_CLI_USE_MSBUILD_SERVER=false
```

Project-side files (so `dotnet run` also obeys them):

```xml
<!-- Directory.Build.props -->
<Project>
  <PropertyGroup>
    <UseSharedCompilation>false</UseSharedCompilation>
    <UseRazorBuildServer>false</UseRazorBuildServer>
    <RestorePackagesPath>$(MSBuildThisFileDirectory).sandbox/nuget/packages</RestorePackagesPath>
  </PropertyGroup>
</Project>
```

```xml
<!-- nuget.config: user/machine configs are not needed once feeds are explicit -->
<configuration>
  <packageSources>
    <clear />
    <add key="nuget.org" value="https://api.nuget.org/v3/index.json" />
  </packageSources>
</configuration>
```

Run `dotnet restore --configfile nuget.config`, then `dotnet build --no-restore --disable-build-servers`, then `dotnet run --no-build`.

What this still leaves outside `$P`:

- Read: the SDK/runtime install dir, `/etc/ssl/certs`, and possibly `/etc/opt/NuGet/Config` (skipped when `--configfile` is used) [S2][S4].
- Write: `/tmp` for MSBuild node and Roslyn compiler-server sockets (hard-coded, **not** `TMPDIR`). Avoid it with `UseSharedCompilation=false` plus `-m:1` **(unverified)**. Otherwise allow `/tmp` [S11][S12].
- With `HOME` set to `$P/home`, the dev cert is generated per sandbox unless `DOTNET_GENERATE_ASPNET_CERTIFICATE=false`. Kestrel HTTPS then has no dev cert, so use an HTTP-only URL (`ASPNETCORE_URLS=http://127.0.0.1:5000`). The HTTP-only fallback is **unverified** here.

## Answer

The CLI needs these locations outside the project: `~/.nuget/packages` (rw), `~/.local/share/NuGet` (rw), `/tmp` (rw), `~/.dotnet` (rw), the SDK/runtime install dir (r), the NuGet config files (r) and `/etc/ssl/certs` (r). On `run` it can also use `~/.aspnet` and `~/.microsoft/usersecrets` depending on the app. Network is needed only for restoring packages that are not already in the global-packages folder, plus audit, telemetry and workload-manifest background calls. To confine everything under one folder, set `HOME`, `DOTNET_CLI_HOME`, `XDG_DATA_HOME`, `NUGET_PACKAGES`, `NUGET_HTTP_CACHE_PATH`, `NUGET_PLUGINS_CACHE_PATH`, `NUGET_SCRATCH` and `TMPDIR` to subfolders of it, opt out of telemetry, and disable build servers. Even then, MSBuild and Roslyn sockets stay under hard-coded `/tmp`, so a sandbox must allow `/tmp` or accept a non-parallel build without the compiler server.

## Sources

- [S1] https://learn.microsoft.com/en-us/nuget/consume-packages/managing-the-global-packages-and-cache-folders
- [S2] https://learn.microsoft.com/en-us/nuget/consume-packages/configuring-nuget-behavior
- [S3] https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-environment-variables
- [S4] https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-restore
- [S5] https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-build
- [S6] https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-environment-variables (`DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE`)
- [S7] https://learn.microsoft.com/en-us/dotnet/core/tools/telemetry
- [S8] https://github.com/dotnet/sdk/blob/main/src/Cli/Microsoft.DotNet.Cli.Definitions/Common/CommonOptions.cs (`--disable-build-servers` forwards `UseRazorBuildServer=false`, `UseSharedCompilation=false`, `/nodeReuse:false`)
- [S9] https://github.com/NuGet/NuGet.Client/blob/dev/src/NuGet.Core/NuGet.Common/PathUtil/NuGetEnvironment.cs
- [S10] https://github.com/dotnet/sdk/blob/main/src/Common/CliFolderPathCalculatorCore.cs
- [S11] https://github.com/dotnet/msbuild/blob/main/src/Shared/NamedPipeUtil.cs
- [S12] https://github.com/dotnet/roslyn/blob/main/src/Compilers/Shared/NamedPipeUtil.cs and https://github.com/dotnet/roslyn/blob/main/src/Compilers/Shared/BuildServerConnection.cs
- [S13] https://learn.microsoft.com/en-us/nuget/concepts/auditing-packages
- [S14] https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-restore (workload manifest downloads)
- [S15] https://github.com/dotnet/sdk/blob/main/src/Cli/Microsoft.DotNet.Configurer/FirstTimeUseNoticeSentinel.cs
- [S16] https://github.com/dotnet/sdk/blob/main/src/Cli/Microsoft.DotNet.Configurer/DotnetFirstTimeUseConfigurer.cs and https://github.com/dotnet/sdk/blob/main/src/Cli/Microsoft.DotNet.Configurer/AspNetCertificateSentinel.cs and https://github.com/dotnet/sdk/blob/main/src/Cli/Microsoft.DotNet.Configurer/UserLevelCacheWriter.cs
- [S17] https://github.com/dotnet/sdk/blob/main/src/Cli/dotnet/SdkVulnerability/SdkReleaseMetadataCache.cs (cache under the `~/.dotnet` path)
- [S18] https://learn.microsoft.com/visualstudio/msbuild/msbuild-command-line-reference (`-nodeReuse`, default true)
- [S19] https://learn.microsoft.com/visualstudio/msbuild/msbuild-server
- [S20] https://github.com/dotnet/sdk/blob/main/src/RazorSdk/Targets/Sdk.Razor.CurrentVersion.targets (`UseRazorBuildServer` defaults to `UseSharedCompilation`)
- [S21] https://github.com/dotnet/runtime/blob/main/src/libraries/System.Private.CoreLib/src/System/IO/PersistedFiles.Unix.cs (`$HOME` then passwd; `.dotnet`/`corefx`)
- [S22] https://learn.microsoft.com/aspnet/core/security/enforcing-ssl (`~/.dotnet/corefx/cryptography/x509stores/my`)
- [S23] https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-environment-variables (`DOTNET_GENERATE_ASPNET_CERTIFICATE`)
- [S24] https://learn.microsoft.com/aspnet/core/security/enforcing-ssl (Linux: trust export to `~/.aspnet/dev-certs/trust`)
- [S25] https://learn.microsoft.com/aspnet/core/security/data-protection/configuration/default-settings and https://learn.microsoft.com/dotnet/api/microsoft.aspnetcore.dataprotection.repositories.filesystemxmlrepository.defaultkeystoragedirectory (`$HOME/.aspnet/DataProtection-Keys`)
- [S26] https://learn.microsoft.com/aspnet/core/security/app-secrets (`~/.microsoft/usersecrets/<id>/secrets.json`)
- [S27] https://learn.microsoft.com/dotnet/standard/security/cross-platform-cryptography (OpenSSL roots, `SSL_CERT_FILE`/`SSL_CERT_DIR`, `/etc/ssl/certs` fallback)
- [S28] https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-build (note: `-property:` not respected when `dotnet run` runs the build)
- [S29] https://learn.microsoft.com/en-us/nuget/consume-packages/managing-the-global-packages-and-cache-folders (retrieval order)
- [S30] https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-build-server
- [S31] https://github.com/dotnet/msbuild/blob/main/documentation/wiki/MSBuild-Environment-Variables.md (`MSBUILDDISABLENODEREUSE=1`)
