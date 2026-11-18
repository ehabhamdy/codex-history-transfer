# Codex History Transfer

Move one project's local Codex chats from macOS to native Windows while
preserving chats already on the destination. This is an unofficial, offline,
one-time migration utility for history stored in `.codex`. It does not export
cloud ChatGPT conversations or copy the project's repository.

The original Mac-to-Windows transfer was reported successful by the user.
Current tool version: **1.1.2**. See [validation and known limits](VALIDATION.md)
for the recorded checks and their scope.

## Requirements

- Python **3.9+** on both computers; no pip packages are required.
- For compressed source sessions, either the `zstd` command on the Mac or
  Python **3.14+** with `compression.zstd` available.
- Compatible Codex database schemas on both computers. Original validation
  used Codex CLI/app-server **0.169.0**; matching app versions is recommended.
- The repository copied or cloned to Windows and opened as a local project in
  the app at least once.
- ChatGPT/Codex, CLI sessions, and background agents stopped before export or
  applying an import.

## Quick start

Run from the folder containing `transfer.py`, replacing the example checkout
paths. See [HOW_TO.md](HOW_TO.md) for the full procedure and recovery steps.

Quit the app and agents on the Mac, then export:

```bash
python3 transfer.py export --project "$HOME/Code/my-project" --out "$HOME/Downloads/my-project-history.zip"
```

Copy `transfer.py` and the ZIP to a folder on Windows using a private transfer
method. Bundles contain conversations and tool output; keep them out of Git
and public storage. From that folder, preview in PowerShell:

```powershell
py -3 transfer.py import --bundle ".\my-project-history.zip" --project "$env:USERPROFILE\Code\my-project"
```

Quit the app and agents on Windows, then apply:

```powershell
py -3 transfer.py import --bundle ".\my-project-history.zip" --project "$env:USERPROFILE\Code\my-project" --apply
```

The import stages the merge, checks preservation of existing database rows,
creates a backup beside the destination data directory, and verifies the
installed sessions and full imported state/history rows. Reopen the app and
check imported and existing chats before continuing work. Keep the originals
and backup until those checks succeed. Duplicate chat IDs are refused; do not
import the same bundle twice.

## Scope and limits

- Transfers active and archived project chats, stored turns/items, and session
  payloads. Remaps working-directory metadata and transcript byte offsets.
- Preserves historical messages and tool output, including original Mac paths.
- Does not copy repository files, worktrees, credentials, configuration,
  plugins, skills, automations, model memory, or external media/output files.
- Does not copy imported sidebar pins or custom sections. Separate worktrees,
  multiple roots, and cross-project fork/agent dependencies are unsupported.
- This is not two-way synchronization. Preview performs fewer checks than the
  staged merge. `verify` checks the import snapshot before chats or settings
  change; see [verification coverage](VALIDATION.md#verification-coverage).

## Documentation and development

- [HOW_TO.md](HOW_TO.md): migration, custom data locations, verification,
  troubleshooting, and manual rollback.
- [VALIDATION.md](VALIDATION.md): user-reported Windows success, recorded
  real-data validation, automated test coverage, and remaining limits.

Run the synthetic regression tests from this directory:

```bash
python3 -m unittest -v
```

Tests use temporary stores and simulated Windows file locking without changing
real Codex history. For CLI options, run `python3 transfer.py --help`; on
Windows, use `py -3` in place of `python3`.
