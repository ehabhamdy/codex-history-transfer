#!/usr/bin/env python3
"""Offline, additive Codex history transfer. Unofficial; never sync live databases.

Python 3.9+. Export needs `zstd` for compressed sessions (or Python 3.14).
Import uses only Python's standard library. Run --help for commands.
"""
import argparse
import bisect
from contextlib import closing, contextmanager
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
import zipfile

VERSION = 1
TOOL_VERSION = '1.1.1'
STATE = 'state_5.sqlite'
HISTORY = 'thread_history_1.sqlite'
STATE_TABLES = ['threads', 'thread_dynamic_tools', 'thread_attachments', 'thread_spawn_edges']
HISTORY_TABLES = ['thread_turns', 'thread_items', 'thread_history_projection_state', 'thread_realtime_items']

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def home_default():
    return Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))

def norm(path):
    value = str(path).replace('\\', '/').rstrip('/')
    return value.casefold() if len(value) > 1 and value[1] == ':' else value

def beneath(path, root):
    return norm(path) == norm(root) or norm(path).startswith(norm(root) + '/')

def ro(path):
    require(path.is_file(), f'Missing database: {path}. Open the project in the app first.')
    con = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    return con

def schema(con, table):
    # Includes types, nullability, defaults, and primary keys, not just names.
    return [list(row) for row in con.execute('PRAGMA table_info("' + table + '")')]

def rows(con, table):
    return [dict(row) for row in con.execute('SELECT * FROM "' + table + '"')]

def digest(data):
    return hashlib.sha256(data).hexdigest()

def mapped_cwd(value, source, destination):
    require(beneath(value, source), 'A session working directory lies outside the exported project.')
    suffix = str(value).replace('\\', '/')[len(str(source).replace('\\', '/').rstrip('/')):].lstrip('/')
    return str(destination.joinpath(*PurePosixPath(suffix).parts))

def mapped_rollout(data, payload, project):
    # Codex restores cwd from the latest turn_context, not just session_meta.
    # Only change structured location metadata; preserve messages and tool outputs.
    output = io.BytesIO()
    old_position = 0
    boundaries = [0]
    deltas = [0]
    first = True
    for line in data.splitlines(keepends=True):
        event = json.loads(line)
        if first:
            require(event.get('type') == 'session_meta', 'Unrecognized session metadata header.')
            first = False
        updated = line
        metadata = []
        if event.get('type') in ('session_meta', 'turn_context'):
            metadata.append(event['payload'])
        elif event.get('type') == 'event_msg' and event.get('payload', {}).get('type') == 'thread_settings_applied':
            metadata.append(event['payload']['thread_settings'])
        elif event.get('type') == 'world_state':
            environments = event.get('payload', {}).get('state', {}).get('environments', {}).get('environments', {})
            metadata.extend(value for value in environments.values() if isinstance(value, dict) and 'cwd' in value)
        for meta in metadata:
            if 'cwd' in meta:
                meta['cwd'] = mapped_cwd(meta['cwd'], payload['source_project'], project)
            for key in ('runtime_workspace_roots', 'workspace_roots'):
                if key in meta and meta[key] is not None:
                    require(all(isinstance(p, str) for p in meta[key]), 'Unsupported workspace-root metadata.')
                    meta[key] = [mapped_cwd(p, payload['source_project'], project)
                                 for p in meta[key] if beneath(p, payload['source_project'])]
        if metadata:
            updated = json.dumps(event, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\n'
        old_position += len(line)
        output.write(updated)
        if len(updated) != len(line):
            boundaries.append(old_position)
            deltas.append(output.tell() - old_position)
    return output.getvalue(), (boundaries, deltas)

def mapped_thread_row(original, payload, home, project, project_id):
    row = dict(original)
    row['cwd'] = mapped_cwd(row['cwd'], payload['source_project'], project)
    relative = next(name for name, info in payload['files'].items() if info['thread_id'] == row['id'])
    row['rollout_path'] = str(home.joinpath(*PurePosixPath(relative).parts))
    row['project_id'] = project_id
    row['is_pinned'] = 0
    row['thread_section_id'] = None
    row['section_position'] = None
    row['section_entered_at_ms'] = None
    return row

def mapped_history_row(original, offsets):
    row = dict(original)
    boundaries, deltas = offsets[row['thread_id']]
    for key in ('rollout_byte_offset', 'rollout_end_byte_offset', 'next_rollout_byte_offset'):
        if row.get(key) is not None:
            row[key] += deltas[bisect.bisect_right(boundaries, row[key]) - 1]
    return row

def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

def read_global(home):
    p = home / '.codex-global-state.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}

def running_apps():
    if os.name == 'nt':
        result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, text=True, check=True)
        names = [line.split(',')[0].strip('"').lower() for line in result.stdout.splitlines()]
        return sorted(set(n for n in names if n.startswith(('chatgpt', 'codex')) and n.endswith('.exe')))
    result = subprocess.run(['ps', '-axo', 'comm='], capture_output=True, text=True, check=True)
    names = [line.strip() for line in result.stdout.splitlines()]
    return sorted(set(n for n in names if '/ChatGPT.app/' in n or '/Codex.app/' in n or Path(n).name in ('codex', 'codex-cli')))

def require_closed():
    active = running_apps()
    require(not active, 'Quit ChatGPT/Codex and stop Codex CLI/background agents first. Still running: ' + ', '.join(active[:5]))

def snapshot(source, destination):
    with closing(ro(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)
        dst.commit()

@contextmanager
def staging_directory(parent):
    stage = Path(tempfile.mkdtemp(prefix='codex-transfer-stage-', dir=parent))
    try:
        yield stage
    finally:
        # Cleanup must never replace the original staging/publish error, or
        # make a successful import look failed because antivirus holds a file.
        try:
            shutil.rmtree(stage)
        except OSError as exc:
            print(f'NOTE: Temporary files remain at {stage}: {exc}', file=sys.stderr)

def rollout_bytes(path):
    if path.exists():
        return path.read_bytes()
    compressed = Path(str(path) + '.zst')
    require(compressed.exists(), f'Session payload missing: {path} (also checked .zst).')
    try:
        from compression import zstd
        return zstd.decompress(compressed.read_bytes())
    except ImportError:
        require(shutil.which('zstd'), 'Compressed sessions need zstd on the exporting computer, or Python 3.14+.')
        return subprocess.run(['zstd', '-q', '-d', '-c', str(compressed)], capture_output=True, check=True).stdout

def make_export(home, project, output):
    with closing(ro(home / STATE)) as state, closing(ro(home / HISTORY)) as history:
        return _make_export(home, project, output, state, history)

def _make_export(home, project, output, state, history):
    require(not output.exists(), f'Refusing to replace {output}')
    global_state = read_global(home)
    project_ids = {r['project_id'] for r in rows(state, 'project_roots') if norm(r['path']) == norm(project)}
    legacy_ids = {key for key, value in global_state.get('local-projects', {}).items()
                  if any(norm(p) == norm(project) for p in value.get('rootPaths', []))}
    assignments = global_state.get('thread-project-assignments', {})
    selected = [r for r in rows(state, 'threads') if beneath(r['cwd'], project)
                or r.get('project_id') in project_ids
                or assignments.get(r['id'], {}).get('projectId') in legacy_ids]
    require(selected, f'No local chats found for {project}')
    require(all(beneath(r['cwd'], project) for r in selected),
            'This project has chats in other roots/worktrees. Export those roots separately after inspecting their dependencies.')
    ids = {r['id'] for r in selected}
    payload = {'format': VERSION, 'source_home': str(home), 'source_project': str(project),
               'exported_at': int(time.time()), 'schemas': {}, 'tables': {}, 'files': {},
               'thread_ids': sorted(ids)}
    for dbname, con, tables in [(STATE, state, STATE_TABLES), (HISTORY, history, HISTORY_TABLES)]:
        payload['schemas'][dbname] = {t: schema(con, t) for t in tables}
        payload['tables'][dbname] = {}
        for table in tables:
            if table == 'threads':
                selected_rows = selected
            elif table == 'thread_spawn_edges':
                selected_rows = [r for r in rows(con, table) if r['child_thread_id'] in ids or r['parent_thread_id'] in ids]
                require(all(r['child_thread_id'] in ids and r['parent_thread_id'] in ids for r in selected_rows),
                        'A linked agent belongs to another project. This export cannot safely omit that dependency.')
            else:
                marks = ','.join('?' for _ in ids)
                selected_rows = [dict(r) for r in con.execute(f'SELECT * FROM "{table}" WHERE thread_id IN ({marks})', sorted(ids))]
            payload['tables'][dbname][table] = selected_rows
    # Export original bytes; import remaps metadata and the associated byte offsets.
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(output.name + '.partial-' + uuid.uuid4().hex)
    try:
        with zipfile.ZipFile(temp_output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for row in selected:
                p = Path(row['rollout_path'])
                relative = p.relative_to(home).as_posix()
                require(relative.startswith(('sessions/', 'archived_sessions/')), 'Unexpected rollout location: ' + relative)
                data = rollout_bytes(p)
                header = json.loads(data.split(b'\n', 1)[0]).get('payload', {})
                for key in ('forked_from_id', 'forked_from', 'parent_thread_id'):
                    parent = header.get(key)
                    require(not parent or (isinstance(parent, str) and parent in ids),
                            f'Chat {row["id"]} has an external fork dependency ({key}); export aborted.')
                require(not row.get('attached_thread_parent_id') or row['attached_thread_parent_id'] in ids,
                        'An attached parent chat is outside this export.')
                archive.writestr(relative, data)
                payload['files'][relative] = {'sha256': digest(data), 'bytes': len(data), 'thread_id': row['id']}
            archive.writestr('manifest.json', json.dumps(payload, ensure_ascii=False))
        os.replace(temp_output, output)
    finally:
        temp_output.unlink(missing_ok=True)
    print(f'Exported {len(ids)} chats to {output}')
    return payload

def validate_bundle(archive):
    require(len(archive.namelist()) == len(set(archive.namelist())), 'Duplicate ZIP entries.')
    payload = json.loads(archive.read('manifest.json'))
    require(payload.get('format') == VERSION, 'Unsupported transfer bundle version.')
    expected = set(payload['files']) | {'manifest.json'}
    require(set(archive.namelist()) == expected, 'Unexpected ZIP contents.')
    for name, info in payload['files'].items():
        parts = PurePosixPath(name).parts
        require(not PurePosixPath(name).is_absolute() and '..' not in parts and '\\' not in name
                and ':' not in name and parts[0] in ('sessions', 'archived_sessions'), 'Unsafe archive path.')
        data = archive.read(name)
        require(len(data) == info['bytes'] and digest(data) == info['sha256'], 'Checksum failed: ' + name)
    return payload

def put_rows(con, table, data):
    if not data:
        return
    columns = list(data[0])
    query = 'INSERT INTO "' + table + '" (' + ','.join('"' + k + '"' for k in columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')'
    con.executemany(query, [[row[k] for k in columns] for row in data])

def plan_import(home, project, payload):
    existing_ids = set()
    for dbname in [STATE, HISTORY]:
        with closing(ro(home / dbname)) as con:
            for table, expected in payload['schemas'][dbname].items():
                require(schema(con, table) == expected,
                        f'Incompatible schema: {dbname}/{table}. Match app versions; do not force this import.')
            if dbname == STATE:
                existing_ids = {r[0] for r in con.execute('SELECT id FROM threads')}
    incoming_ids = set(payload['thread_ids'])
    overlap = incoming_ids & existing_ids
    # Never silently skip a diverged chat: reruns need deliberate reconciliation.
    require(not overlap, f'{len(overlap)} chat ID(s) already exist on the destination. Nothing overwritten. Do not import the same bundle twice.')
    require(project.is_dir(), f'The destination repository folder does not exist: {project}')
    print(f'Plan: add {len(incoming_ids)} chats; preserve {len(existing_ids)} existing chats.')
    print(f'Project mapping: {payload["source_project"]} -> {project}')
    return existing_ids

def prepare_merge(home, project, payload, archive, stage):
    offsets = {}
    for name, info in payload['files'].items():
        data, shifts = mapped_rollout(archive.read(name), payload, project)
        offsets[info['thread_id']] = shifts
        target = stage.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for name in [STATE, HISTORY]:
        snapshot(home / name, stage / name)
    with closing(sqlite3.connect(stage / STATE)) as state, closing(sqlite3.connect(stage / HISTORY)) as history:
        state.row_factory = sqlite3.Row
        history.row_factory = sqlite3.Row
        baseline_fk = {id(con): {tuple(row) for row in con.execute('PRAGMA foreign_key_check')}
                       for con in (state, history)}
        now = int(time.time() * 1000)
        target_projects = {r['project_id'] for r in rows(state, 'project_roots') if norm(r['path']) == norm(project)}
        require(len(target_projects) <= 1, 'Multiple destination projects use this folder. Resolve that ambiguity first.')
        project_id = next(iter(target_projects), str(uuid.uuid4()))
        if not target_projects:
            columns = {r[1] for r in schema(state, 'projects')}
            require(columns == {'id', 'name', 'metadata', 'position', 'created_at_ms', 'updated_at_ms'}, 'Unsupported projects schema.')
            position = state.execute('SELECT coalesce(max(position),0)+1 FROM projects').fetchone()[0]
            put_rows(state, 'projects', [{'id': project_id, 'name': project.name, 'metadata': '{}', 'position': position, 'created_at_ms': now, 'updated_at_ms': now}])
            put_rows(state, 'project_roots', [{'project_id': project_id, 'position': 0, 'path': str(project)}])
        for table, data in payload['tables'][STATE].items():
            adjusted = []
            for original in data:
                row = dict(original)
                if table == 'threads':
                    row = mapped_thread_row(original, payload, home, project, project_id)
                elif table == 'thread_attachments':
                    require(row['attachment_type'] == 'pull_request',
                            'This project has local worktree/file attachments that need a separate migration. Aborting.')
                adjusted.append(row)
            put_rows(state, table, adjusted)
        for table, data in payload['tables'][HISTORY].items():
            put_rows(history, table, [mapped_history_row(row, offsets) for row in data])
        state.commit()
        history.commit()
        for con in (state, history):
            require(con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Staged database integrity check failed.')
            new_fk = {tuple(row) for row in con.execute('PRAGMA foreign_key_check')} - baseline_fk[id(con)]
            require(not new_fk, f'Imported rows introduce invalid database references: {sorted(new_fk)}')
            con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    g = read_global(home)
    locals_ = g.setdefault('local-projects', {})
    matches = [key for key, value in locals_.items() if any(norm(p) == norm(project) for p in value.get('rootPaths', []))]
    require(len(matches) <= 1, 'Multiple sidebar projects use this folder.')
    legacy_id = matches[0] if matches else str(uuid.uuid4())
    if not matches:
        locals_[legacy_id] = {'id': legacy_id, 'name': project.name, 'rootPaths': [str(project)], 'createdAt': now, 'updatedAt': now}
    for thread_id in payload['thread_ids']:
        g.setdefault('thread-project-assignments', {})[thread_id] = {'projectKind': 'local', 'projectId': legacy_id}
        g.setdefault('thread-project-membership-host-ids', {})[thread_id] = 'local'
    write_json(stage / '.codex-global-state.json', g)
    for name in ['session_index.jsonl']:
        old = (home / name).read_bytes() if (home / name).exists() else b''
        if old and not old.endswith(b'\n'):
            old += b'\n'
        entries = [{'id': r['id'], 'thread_name': r.get('title') or '',
                    'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(r['updated_at']))}
                   for r in payload['tables'][STATE]['threads']]
        (stage / name).write_bytes(old + b''.join((json.dumps(r, ensure_ascii=False) + '\n').encode() for r in entries))

def apply_import(home, project, bundle, check_closed=True):
    if check_closed:
        require_closed()
    with zipfile.ZipFile(bundle) as archive:
        payload = validate_bundle(archive)
        existing_ids = plan_import(home, project, payload)
        for name in payload['files']:
            target = home.joinpath(*PurePosixPath(name).parts)
            require(not target.exists() and not Path(str(target) + '.zst').exists(), 'Session file already exists: ' + str(target))
        print('Preparing the merge in temporary files; existing history has not been changed.')
        with staging_directory(home.parent) as stage:
            prepare_merge(home, project, payload, archive, stage)
            # Confirm that the merge preserved every original database row verbatim.
            for dbname in [STATE, HISTORY]:
                with closing(ro(home / dbname)) as old, closing(sqlite3.connect(stage / dbname)) as new:
                    new.execute('ATTACH DATABASE ? AS original', (str(home / dbname),))
                    for (table,) in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                        require(not new.execute(f'SELECT * FROM original."{table}" EXCEPT SELECT * FROM main."{table}" LIMIT 1').fetchall(),
                                'Existing rows changed in ' + dbname + '/' + table)
            if check_closed:
                require_closed()
            backup = home.parent / ('codex-history-backup-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
            backup.mkdir(mode=0o700)
            core = [STATE, HISTORY, '.codex-global-state.json', 'session_index.jsonl']
            missing = []
            for name in core:
                if not (home / name).exists():
                    missing.append(name)
                elif name.endswith('.sqlite'):
                    snapshot(home / name, backup / name)
                else:
                    shutil.copy2(home / name, backup / name)
            write_json(backup / 'restore.json', {'home': str(home), 'core': core, 'originally_missing': missing,
                                               'added_files': list(payload['files']), 'thread_ids': payload['thread_ids']})
            print(f'Pre-import backup: {backup}')
            published = []
            try:
                for name in payload['files']:
                    dest = home.joinpath(*PurePosixPath(name).parts)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(stage.joinpath(*PurePosixPath(name).parts), dest)
                    published.append(dest)
                for name in core:
                    for suffix in ['-wal', '-shm'] if name.endswith('.sqlite') else []:
                        (home / (name + suffix)).unlink(missing_ok=True)
                    os.replace(stage / name, home / name)
                verify_import(home, bundle, project)
            except Exception:
                for name in core:
                    if name in missing:
                        (home / name).unlink(missing_ok=True)
                    else:
                        shutil.copy2(backup / name, home / name)
                for path in published:
                    path.unlink(missing_ok=True)
                raise
            print(f'Backup for rollback: {backup}')
            print('Reopen the app. Check the oldest and newest imported chats before continuing them.')
            print(f'CLI fallback: codex resume {payload["thread_ids"][0]} --cd "{project}"')
            return backup

def verify_import(home, bundle, project=None):
    with zipfile.ZipFile(bundle) as archive:
        payload = validate_bundle(archive)
        if project is None:
            with closing(ro(home / STATE)) as con:
                tid = payload['thread_ids'][0]
                record = con.execute('SELECT cwd FROM threads WHERE id=?', (tid,)).fetchone()
                require(record, 'Imported chat not found.')
                source_row = next(r for r in payload['tables'][STATE]['threads'] if r['id'] == tid)
                suffix = str(source_row['cwd']).replace('\\', '/')[len(str(payload['source_project']).replace('\\', '/').rstrip('/')):].lstrip('/')
                project = Path(record['cwd'])
                for part in PurePosixPath(suffix).parts:
                    project = project.parent
        offsets = {}
        for name, info in payload['files'].items():
            expected, shifts = mapped_rollout(archive.read(name), payload, project)
            offsets[info['thread_id']] = shifts
            path = home.joinpath(*PurePosixPath(name).parts)
            require(path.exists() and digest(path.read_bytes()) == digest(expected), 'Session verification failed: ' + name)
    ids = set(payload['thread_ids'])
    for dbname, tables in payload['tables'].items():
        with closing(ro(home / dbname)) as con:
            if dbname == STATE:
                # Resolve the expected project independently of imported thread values.
                target_projects = {r['project_id'] for r in rows(con, 'project_roots')
                                   if norm(r['path']) == norm(project)}
                require(len(target_projects) == 1, 'Missing or ambiguous destination project mapping.')
                project_id = next(iter(target_projects))
                require(con.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone(),
                        'Destination project not found.')
            for table, expected in tables.items():
                if table == 'thread_spawn_edges':
                    actual = [r for r in rows(con, table) if r['child_thread_id'] in ids]
                else:
                    key = 'id' if table == 'threads' else 'thread_id'
                    marks = ','.join('?' for _ in ids)
                    actual = [dict(r) for r in con.execute(f'SELECT * FROM "{table}" WHERE "{key}" IN ({marks})', sorted(ids))]
                require(len(actual) == len(expected), f'Row count mismatch: {dbname}/{table}')
                if dbname == HISTORY:
                    expected = [mapped_history_row(row, offsets) for row in expected]
                elif table == 'threads':
                    expected = [mapped_thread_row(row, payload, home, project, project_id) for row in expected]
                label = 'History' if dbname == HISTORY else 'State'
                require(sorted(json.dumps(r, sort_keys=True) for r in actual) == sorted(json.dumps(r, sort_keys=True) for r in expected),
                        f'{label} content mismatch: {table}')
    print(f'Verified: {len(ids)} chats, mapped session files, and full state/history row content.')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version='codex-history-transfer ' + TOOL_VERSION)
    parser.add_argument('--debug', action='store_true', help='Show the original exception traceback on failure.')
    parser.add_argument('--codex-home', type=Path, default=home_default())
    commands = parser.add_subparsers(dest='command', required=True)
    exp = commands.add_parser('export', help='Export one project; app and agents must be closed.')
    exp.add_argument('--project', type=Path, required=True)
    exp.add_argument('--out', type=Path, required=True)
    imp = commands.add_parser('import', help='Validate/preview by default; use --apply to merge offline.')
    imp.add_argument('--project', type=Path, required=True)
    imp.add_argument('--bundle', type=Path, required=True)
    imp.add_argument('--apply', action='store_true')
    ver = commands.add_parser('verify', help='Check the import against the original bundle.')
    ver.add_argument('--bundle', type=Path, required=True)
    args = parser.parse_args()
    home = args.codex_home.expanduser().resolve()
    if args.command == 'export':
        require_closed()
        make_export(home, args.project.expanduser().resolve(), args.out.expanduser().resolve())
    elif args.command == 'import':
        project = args.project.expanduser().resolve()
        if args.apply:
            apply_import(home, project, args.bundle)
        else:
            with zipfile.ZipFile(args.bundle) as archive:
                payload = validate_bundle(archive)
                plan_import(home, project, payload)
            print('Preview only; no files changed. Quit the app and rerun with --apply to merge.')
    else:
        verify_import(home, args.bundle)

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, sqlite3.Error, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        if '--debug' in sys.argv:
            traceback.print_exc()
        print('STOP: ' + str(exc), file=sys.stderr)
        sys.exit(1)
