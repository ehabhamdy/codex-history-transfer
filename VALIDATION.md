# Validation

[Project overview](README.md) · [Migration guide](HOW_TO.md)

## Windows transfer outcome

On **2026-11-17**, the user reported that transferring the project's sessions
to their Windows machine worked properly. Windows app/Python versions and
detailed UI checks were not recorded. Automated checks below ran on macOS,
including simulations of Windows file locking.

## Current repository checks — 2026-11-17

After the verification fix, `python3 -m unittest -bv` passed all **eight**
regression tests using Python **3.12.13** on macOS (`-b` buffers test output).
CLI help was checked against the documented commands. Tests use synthetic
chats and temporary stores, without modifying production history.

The suite covers:

- Preserving the original staging error and closing SQLite handles.
- Preventing cleanup failures from hiding a primary error.
- Preserving an existing foreign-key defect and chat while a merge succeeds.
- Rejecting new foreign-key defects without leaking handles.
- Restoring previous history after failed post-install verification, with
  simulated Windows restrictions on open database handles.
- Rejecting an incorrect imported `rollout_path` and closing verification
  handles on failure.
- Accepting remapped subdirectory chats, a reused destination project, and
  reset source sidebar fields, then rejecting changed values across all four
  imported state tables (11 mutation subcases).
- Restoring both databases and removing newly installed files after a state
  verification failure.

Run with `python3 -m unittest -v` on macOS or `py -3 -m unittest -v` on
Windows. The locking simulation does not replace a native Windows test run.

## Original real-data validation — 2026-11-17

These results were recorded during the original implementation and were not
rerun during the documentation review. Source: the original `ehapps-website`
project on macOS, with **10 chats**. Runtime: Codex CLI/app-server **0.169.0**.
Tests used isolated copies without modifying production history or project
repository files.

- Exported compressed and uncompressed sessions into isolated destination
  databases using the app's schemas.
- Preserved an existing chat, its history, project association/preferences,
  and authentication/configuration files.
- Checked that all preexisting database rows remained unchanged before
  installing the staged merge.
- Refused a duplicate import and incompatible schema.
- Preserved message/tool-output bytes while remapping metadata and verified
  **207** paginated byte offsets.
- Tested Windows paths with a drive letter, backslashes, and spaces using
  `PureWindowsPath`.
- The official app-server read all **10 chats** and **99 stored turns** with
  the remapped checkout. `thread/resume` initialized the oldest imported chat
  there; no model turn was submitted.
- Python syntax compilation passed.

## Version 1.1.0 regression record

The reported Windows staging failure led to the original five synthetic tests.
The recorded validation reproduced the locked-file cleanup error pattern,
passed the suite and syntax check, and retained bundle format version **1**.
It did not establish which original exception had been masked on the user's
Windows computer. `--debug` exposes the traceback if it recurs. See the
[staging-error troubleshooting note](HOW_TO.md#version-10-windows-staging-error).

## Version 1.1.1 verification fix

The review reproduced a limitation in `verify_import` in
[transfer.py](transfer.py): changing an imported thread's `rollout_path` in
`state_5.sqlite` to a nonexistent file still allowed verification to pass.
The three new regression tests failed against the old implementation and now
pass. Version **1.1.1** fixes the gap without changing bundle format **1**.

## Verification coverage

Verification compares mapped session bytes and full history-table content,
including adjusted transcript offsets. It also compares full imported rows
in `threads`, `thread_dynamic_tools`, `thread_attachments`, and
`thread_spawn_edges`. Expected thread values account for remapped working
directories/session paths, the destination project ID, and cleared pins and
section fields. The destination project is resolved from `project_roots` and
checked for existence independently of imported thread values.

A separate pre-install check protects existing database rows. Verification
does not compare `.codex-global-state.json` sidebar assignments or
`session_index.jsonl`, and it does not exercise the graphical app. It is a
snapshot comparison before continuing or reorganizing chats; inspect the app
as outlined in [the guide](HOW_TO.md#5-verify-and-resume).

## Remaining coverage limits

- No automated native Windows execution or graphical sidebar test is recorded.
- Sending a model request was not part of the recorded real-data checks.
- Arbitrary future app schemas have not been validated.
- Separate worktrees, multi-root projects, and cross-project dependencies are
  outside the supported migration scope.
- External media, attachment, and output file migration is not implemented.
