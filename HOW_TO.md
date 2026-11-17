# Move local Codex chats from Mac to Windows

[Project overview](README.md) · [Validation and limits](VALIDATION.md)

This guide transfers one local project's history to native Windows. Git does
not copy local Codex chats; WSL also has a separate home directory. Check the
[requirements](README.md#requirements) before starting.

## 1. Prepare both computers

Use the same account and workspace, with matching app versions where possible.
Copy or clone the repository to Windows, open/add it as a local project in the
app once, and quit. Verify Python with `python3 --version` on macOS and
`py -3 --version` in Windows PowerShell.

Commands below run from the folder containing `transfer.py`. Replace the
example checkout paths and bundle name with your own values.

History defaults to `$HOME/.codex` on macOS and `%USERPROFILE%\.codex` on
Windows. `CODEX_HOME` overrides the default; the global `--codex-home PATH`
option overrides both. Put global options **before** the command:

```powershell
py -3 transfer.py --codex-home "D:\CodexData" import --bundle ".\my-project-history.zip" --project "D:\Code\my-project"
```

Use the same data location for preview, apply, and verification.

## 2. Export on the Mac

Finish conversations you want included. Quit ChatGPT/Codex completely,
including CLI sessions and background agents, then run:

```bash
python3 transfer.py export --project "$HOME/Code/my-project" --out "$HOME/Downloads/my-project-history.zip"
```

The export includes active and archived project chats, stored turn/item
history, and session payloads. Compressed sessions are decompressed into the
bundle. The original history is not edited, and an existing output ZIP is
never overwritten. The command reports the number of exported chats.

Copy `transfer.py` and the ZIP to a Windows folder such as
`Downloads\codex-history-transfer` using a private transfer method. The ZIP
contains conversations and tool output; do not commit or publish it.

## 3. Preview on Windows

Open PowerShell and run:

```powershell
cd "$env:USERPROFILE\Downloads\codex-history-transfer"
py -3 transfer.py import --bundle ".\my-project-history.zip" --project "$env:USERPROFILE\Code\my-project"
```

Expect `Plan: add N chats; preserve M existing chats`, the project mapping,
and `Preview only; no files changed`. Check the paths and counts.

Preview validates bundle checksums, selected table schemas, duplicate chat
IDs, and the destination folder. It does not stage the merge; attachment,
project-mapping, and database-integrity checks also run with `--apply`.
Existing chat IDs or incompatible schemas stop the import rather than
allowing history to be overwritten.

## 4. Apply the merge

Quit ChatGPT/Codex and all CLI/background agents on Windows. The script also
checks for running processes. Repeat the command with `--apply`:

```powershell
py -3 transfer.py import --bundle ".\my-project-history.zip" --project "$env:USERPROFILE\Code\my-project" --apply
```

The script stages the merge, checks database integrity and newly introduced
foreign-key defects, and checks that existing database rows remain unchanged.
It creates a `codex-history-backup-...` directory beside the destination data
directory before installing the merge. Retain the printed backup path.

Imported chats are associated with the Windows project. Structured location
metadata and transcript byte offsets are remapped; historical messages and
command outputs retain their original text. Existing chats, project settings,
authentication, and configuration are preserved. Imported sidebar pins and
custom sections are not copied.

Post-install verification compares mapped session files and history rows with
the bundle and compares every imported state-table row with its expected
values, including remapped paths, destination project IDs, and reset sidebar
fields. See [verification coverage](VALIDATION.md#verification-coverage).
Do not import the same bundle twice; duplicate IDs are refused.

## 5. Verify and resume

For another strict check, run this while the app is still closed, before
continuing imported chats:

```powershell
py -3 transfer.py verify --bundle ".\my-project-history.zip"
```

Continuing chats or changing their titles, pins, sections, or project
assignments changes the verified snapshot, so a later mismatch does not by
itself mean the transfer failed.

Reopen the app. Inspect an old imported conversation and the newest one, and
confirm that existing Windows chats remain present. Before real work, ask an
imported chat to report its current working directory without changing files;
it should point to the Windows checkout or its corresponding subdirectory.
Choose permissions appropriate for Windows.

If a chat is not grouped in the sidebar, search for its title. If Codex CLI
is installed, use the fallback printed by the import, replacing `CHAT_ID`:

```powershell
codex resume CHAT_ID --cd "$env:USERPROFILE\Code\my-project"
```

Keep the original stores, bundle, and backup until these checks succeed.

## Other projects and unsupported cases

Repeat with another source project, a distinct ZIP, and the matching Windows
checkout. Imports are additive; this is not two-way synchronization. Do not
synchronize a live `.codex` directory between computers.

Separate worktrees, multiple roots, and cross-project fork/agent dependencies
need mapping beyond this utility's scope. Local worktree/file attachment
records are also rejected. Do not bypass these checks. External attachments,
media, and output files must be copied separately where needed; see the
[scope and limits](README.md#scope-and-limits).

## Troubleshooting

- **Running process:** stop the named app/agent and retry.
- **Missing database:** check the data directory and open the destination
  project in the app once.
- **Compressed session needs `zstd`:** make `zstd` available on the exporting
  Mac, or use Python 3.14+ with `compression.zstd`.
- **Incompatible schema:** match app versions; do not bypass the check.
- **Duplicate chat ID:** inspect the existing chat; reimport is unsupported.
- **Other failure:** retain the full output, both original stores, and any
  backup path. Put `--debug` before the command for a traceback:

```powershell
py -3 transfer.py --debug import --bundle ".\my-project-history.zip" --project "$env:USERPROFILE\Code\my-project" --apply
```

A failure before `Pre-import backup` occurs before merged history is installed.
For later failures, the script attempts to restore the previous state when
installation or verification raises an exception. Keep the backup; do not
remove databases or history folders to force discovery.

### Version 1.0 Windows staging error

If an older script stopped with `WinError 267` on `thread_history_1.sqlite`
inside a `codex-transfer-stage-*` directory, replace it with the current script.
Check with `py -3 transfer.py --version`; expect
`codex-history-transfer 1.1.1` for the current release. The existing bundle can
be reused because the bundle format is unchanged.

Version 1.1.0 closes SQLite handles on error, prevents cleanup failures from
masking the original exception, and rejects new foreign-key defects while
tolerating unchanged preexisting defects. Retry with `--debug` if needed.

### Version 1.1.1 verification update

Earlier versions checked imported state-table row counts without comparing
their values. Version 1.1.1 also compares the full rows, so an incorrect stored
session path now raises `State content mismatch: threads`. A mismatch during
installation triggers the existing rollback procedure. The standalone
`verify` command only reports the mismatch; it does not repair history.

Existing format-1 bundles work with this version. For an untouched import,
quit the app and rerun `verify` using the updated script; do not reimport the
bundle. For chats already continued or reorganized, snapshot mismatches can
be expected as described above.

## Manual rollback

Restore before starting new work if possible: a snapshot restore also removes
changes made after it. There is no rollback CLI command.

1. Quit ChatGPT/Codex and all agents. Preserve a second copy of the current
   destination data directory before recovery.
2. Open `restore.json` in the printed backup directory. It records the
   destination `home`, four `core` files, `originally_missing` files, and
   newly `added_files`.
3. Restore `state_5.sqlite`, `thread_history_1.sqlite`,
   `.codex-global-state.json`, and `session_index.jsonl` from the backup to
   the recorded destination. For any file in `originally_missing`, remove
   its newly created counterpart instead.
4. With processes still stopped, remove stale `-wal` and `-shm` sidecars for
   those two databases. Remove only session files listed in `added_files`,
   resolving paths relative to the recorded `home`.
5. Reopen the app and check the original Windows chats. Keep the backup until
   recovery is verified.
