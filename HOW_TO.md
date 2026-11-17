# Move local Codex chats from Mac to Windows

## Update 1.1.0: Windows staging error

If version 1.0 stopped with WinError 267 on thread_history_1.sqlite inside a
codex-transfer-stage-* folder, replace transfer.py with this version. Keep
using your existing ehapps-history.zip; no new export is needed.

In the folder containing both files, verify the new version:

```powershell
python transfer.py --version
```

Expected: codex-history-transfer 1.1.0

Quit ChatGPT/Codex and retry:

```powershell
python transfer.py --debug import --bundle ".\ehapps-history.zip" --project "C:\Users\Ehab\Code\ehapps-website" --apply
```

The original script could leave SQLite connections open on exception, allowing
Windows temporary-directory cleanup to obscure the original error. This update
closes connections on all paths and preserves original errors. It also compares
foreign-key validation against the pre-import baseline so existing application
references do not falsely count as new import defects. Existing rows remain
unchanged; newly introduced defects are rejected.

If it still fails, retain the complete output/traceback. A failure before the
"Pre-import backup" message occurs before installing the merged history. Do not
replace the Windows .codex directory or re-export merely to address this error.

This is an unofficial, version-checked, offline merge utility, not an OpenAI
import feature. It was tested with copies of this Mac's actual 10 project chats
and Codex 0.169.0. It has not been run on your Windows computer or verified in
the Windows graphical app. Keep both computers' original data until the final
Windows checks succeed.

## Why Git did not copy the chats

The repository and local Codex history are separate. This Mac's history lives
in /Users/I575965/.codex. Native Windows uses %USERPROFILE%\.codex unless
CODEX_HOME overrides it. WSL has a separate home; these instructions target
the native Windows app.

## 1. Prepare both computers

Use the same ChatGPT account and workspace. Update the desktop apps to matching
versions if possible. The utility refuses incompatible database schemas.
On Windows, open/add the existing local repository as a project once, then quit.
Python 3.9+ is required on both computers. The Mac already has Python and zstd.
If Windows has no Python, install Python 3 from python.org, then verify:

```powershell
py -3 --version
```

## 2. Export on the Mac

Finish this conversation first. Quit ChatGPT/Codex completely, including CLI
sessions and background agents. Run these commands in macOS Terminal:

```bash
cd "$HOME/Documents/Code/Projects/codex-history-transfer"
python3 transfer.py export --project "/Users/I575965/Documents/Code/Projects/ehapps-website" --out "ehapps-history.zip"
```

The export reports the number of chats. It includes active and archived chats
associated with this project/root, their stored turn/item history, and their
session payloads. Older compressed sessions are decompressed into the bundle.
The original history is never edited. No exported bundle is provided in this
kit because this conversation must finish before it can be fully exported.

## 3. Transfer the folder privately

Copy the whole codex-history-transfer folder, including transfer.py and your
new ehapps-history.zip, to your Windows Downloads folder using USB or another
private transfer method. The ZIP contains your conversations and tool output.
Do not commit it to the website repository or publish it.

## 4. Preview on Windows

Open a normal PowerShell window. Change the example repository path below to
the actual Windows checkout path. The first command previews only:

```powershell
cd "$env:USERPROFILE\Downloads\codex-history-transfer"
py -3 transfer.py import --bundle ".\ehapps-history.zip" --project "C:\Users\Ehab\Code\ehapps-website"
```

Expected: "Plan: add 10 chats; preserve N existing chats" (counts can differ if
you create more chats before export). An existing ID causes a stop, never an
overwrite. A schema mismatch also causes a stop. Do not bypass either check.

## 5. Apply the merge

Quit ChatGPT/Codex fully on Windows. The script also checks running processes.
Then run the same command with --apply:

```powershell
py -3 transfer.py import --bundle ".\ehapps-history.zip" --project "C:\Users\Ehab\Code\ehapps-website" --apply
```

It builds the merge on copies, checks that all existing database rows remain
unchanged, creates a backup beside .codex, and then installs the merged data.
It preserves existing Windows chats, projects, auth, and configuration. It
maps the imported chats to the Windows project, rewrites saved location
metadata, and adjusts paginated transcript byte offsets. Historical messages
and command outputs retain their original text, including original Mac paths.
Imported sidebar pins/custom sections are not copied.

The final "Verified" message checks every exported history row and mapped
session file. Do not run the same import twice; duplicate IDs are refused.

## 6. Check before resuming work

Reopen the app and inspect the project's chats. Check an old conversation and
the newest one, and check that your existing Windows chats are still present.
For an imported conversation, first ask it to report the current working
directory without changing files; it must point to your Windows checkout.
Choose the permissions appropriate for Windows before continuing real work.

If a chat is not grouped in the sidebar, search for its title or open it with
the CLI fallback printed by the import. Do not repeatedly reimport.

If Codex CLI is installed, the fallback is:

```powershell
codex resume CHAT_ID --cd "C:\Users\Ehab\Code\ehapps-website"
```

Optional strict file verification (before continuing chats, since continuation
changes their files):

```powershell
py -3 transfer.py verify --bundle ".\ehapps-history.zip"
```

## 7. Other projects

Repeat steps 2-6 with another Mac --project path, a distinct output ZIP name,
and the matching Windows --project path. Imports are additive. Keep the ZIPs
outside the repositories. This is a one-time migration, not two-way chat sync.
If a project has chats in separate worktrees, multiple roots, or cross-project
fork/agent dependencies, the tool can refuse it: those cases need an explicit
mapping and should not be forced through this single-root utility.

## Limits

- The Windows graphical app has not been tested here. Final Windows inspection
  is required; the code cannot guarantee compatibility with future app builds.
- Repositories, uncommitted work, worktrees, plugins, skills, credentials,
  automations, model memory, and arbitrary external attachment/output files
  are not transferred. Embedded transcript data is retained; links to media
  or files that existed only on the Mac may need those files copied separately.
- Authentication and machine-specific configuration stay on each computer.
- Do not put the live .codex directory in a bidirectional sync folder.
- If an import stops, retain its error and both original stores; do not delete
  databases or history folders to force discovery.

## Rollback if Windows validation fails

Do this before starting new work after the import; restoring a snapshot also
removes changes made since that snapshot. Quit ChatGPT/Codex and all agents.
The script prints a codex-history-backup-... directory beside .codex. Its
restore.json identifies the destination home, four backed-up state files, and
the newly added session files. Preserve a second copy of the current .codex
before recovery. Restore the backed-up state_5.sqlite, thread_history_1.sqlite,
.codex-global-state.json and session_index.jsonl to their recorded location;
for originally_missing files, remove the newly created counterpart. With all
processes stopped, remove stale -wal/-shm sidecars for those two databases and
remove only the added_files listed in restore.json. Reopen the app. Keep the
backup until you have verified your original Windows chats.

## Official references

- [ChatGPT desktop app for Windows](https://learn.chatgpt.com/docs/windows/windows-app)
- [Projects and chats](https://learn.chatgpt.com/docs/projects)
- [Codex app server](https://learn.chatgpt.com/docs/app-server)
