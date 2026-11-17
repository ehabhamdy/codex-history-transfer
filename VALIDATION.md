# Validation

Performed on 2026-11-17 on macOS.

Source: this computer's ehapps-website project; 10 chat records.
Runtime: official Codex CLI/app-server 0.169.0.

## Passed

- Exported actual compressed and uncompressed session payloads.
- Imported into isolated databases using the current app's schemas.
- Preserved an existing destination chat and its history verbatim.
- Preserved existing project association/preferences and auth/config files.
- Automatically checked that every preexisting destination database row
  remained present and unchanged before publishing the staged merge.
- Refused a duplicate import and incompatible database schema.
- Preserved historical message/tool-output bytes while remapping location
  metadata and verified 207 paginated byte offsets.
- Tested native Windows path construction with a drive letter, backslashes,
  and spaces using Python's PureWindowsPath.
- Official app-server read all 10 imported chats and 99 stored turns with
  the remapped checkout; thread/resume initialized the oldest imported chat
  at the new checkout. No model turn was submitted.
- Python syntax compilation passed.

## Not tested

- Execution on Windows itself or the Windows graphical sidebar.
- Sending a model request from an imported chat on Windows.
- Arbitrary future app database schemas.
- Separate worktree/multi-root projects or cross-project dependencies.
- External media/attachment/output file migration.

Production history and repository files were not modified. Tests used
isolated temporary stores. Create the final export after this chat finishes
and the app is closed, so the bundle includes the completed conversation.

## Update 1.1.0 validation — 2026-11-17

- Reproduced the reported error pattern using a deterministic simulation of
  Windows locked SQLite files during temporary-directory cleanup.
- Five synthetic regression tests passed: original staging error remains
  visible; independent cleanup failure cannot mask it; preexisting foreign-key
  defects remain unchanged while a valid merge succeeds; new defects fail;
  failed post-install verification closes handles and restores old history.
- All SQLite connection owners now close their handles on exception as well
  as success, including export, snapshot, validation, and rollback checks.
- Python syntax check passed. Bundle format remains version 1.
- This does not establish which original exception was masked on the user's
  Windows computer; --debug now exposes that exception if it recurs.
- Native Windows execution remains unverified here.
