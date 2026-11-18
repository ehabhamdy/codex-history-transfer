"""Regression tests use synthetic chats and model Windows locked-file cleanup."""
from contextlib import closing
from pathlib import Path
from unittest import mock
import importlib.util
import io
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('transfer', Path(__file__).with_name('transfer.py'))
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)

def create_store(home, project, thread_id):
    home.mkdir()
    project.mkdir()
    state = sqlite3.connect(home / transfer.STATE)
    state.executescript('''
        CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, metadata TEXT, position INTEGER, created_at_ms INTEGER, updated_at_ms INTEGER);
        CREATE TABLE project_roots (project_id TEXT REFERENCES projects(id), position INTEGER, path TEXT);
        CREATE TABLE thread_sections (id TEXT PRIMARY KEY);
        CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT, rollout_path TEXT, title TEXT, updated_at INTEGER,
          project_id TEXT REFERENCES projects(id), is_pinned INTEGER, thread_section_id TEXT REFERENCES thread_sections(id),
          section_position INTEGER, section_entered_at_ms INTEGER, attached_thread_parent_id TEXT);
        CREATE TABLE thread_dynamic_tools (thread_id TEXT REFERENCES threads(id), position INTEGER);
        CREATE TABLE thread_attachments (id TEXT PRIMARY KEY, thread_id TEXT REFERENCES threads(id), attachment_type TEXT);
        CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT PRIMARY KEY);
    ''')
    rollout = home / 'sessions' / ('rollout-' + thread_id + '.jsonl')
    rollout.parent.mkdir()
    rollout.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': thread_id, 'cwd': str(project)}}) + '\n', encoding='utf-8')
    state.execute('INSERT INTO threads VALUES (?,?,?,?,?,NULL,0,NULL,NULL,NULL,NULL)',
                  (thread_id, str(project), str(rollout), 'Synthetic chat', 1000))
    state.commit()
    state.close()
    history = sqlite3.connect(home / transfer.HISTORY)
    history.executescript('''
        CREATE TABLE thread_turns (thread_id TEXT, turn_id TEXT, rollout_byte_offset INTEGER, rollout_end_byte_offset INTEGER);
        CREATE TABLE thread_items (thread_id TEXT, item_id TEXT, item_json TEXT);
        CREATE TABLE thread_history_projection_state (thread_id TEXT PRIMARY KEY, next_rollout_byte_offset INTEGER);
        CREATE TABLE thread_realtime_items (thread_id TEXT, item_id TEXT, item_json TEXT);
    ''')
    history.execute('INSERT INTO thread_items VALUES (?,?,?)', (thread_id, 'item', '{"text":"Preserve this message"}'))
    history.commit()
    history.close()

class WindowsHandleSimulation:
    """Windows cannot unlink open SQLite files; older Python cleanup masks that."""
    def __init__(self):
        self.live = set()
        self.connect = sqlite3.connect

    def open(self, database, *args, **kwargs):
        tracker = self
        class Tracked(sqlite3.Connection):
            def close(self):
                try:
                    super().close()
                finally:
                    tracker.live.discard(self)
        kwargs['factory'] = Tracked
        connection = self.connect(database, *args, **kwargs)
        connection.test_path = str(database)
        self.live.add(connection)
        return connection

    def clean_stage(self, path):
        for con in self.live:
            if 'codex-transfer-stage-' in con.test_path:
                raise NotADirectoryError(267, 'The directory name is invalid', con.test_path)

    def close_all(self):
        for con in list(self.live):
            con.close()

class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / 'mac-home'
        self.target = self.base / 'windows-home'
        self.source_project = self.base / 'mac-project'
        self.project = self.base / 'windows-project'
        create_store(self.source, self.source_project, 'incoming')
        create_store(self.target, self.project, 'existing')
        self.bundle = self.base / 'history.zip'
        transfer.make_export(self.source, self.source_project, self.bundle)
        self.tracker = WindowsHandleSimulation()

    def tearDown(self):
        self.tracker.close_all()
        self.temp.cleanup()

    def test_export_import_compressed_rollouts(self):
        relative = 'sessions/rollout-incoming.jsonl'
        rollout = self.source / relative
        original = rollout.read_bytes()
        compressed = Path(str(rollout) + '.zst')
        try:
            from compression import zstd
        except ImportError:
            if not shutil.which('zstd'):
                self.skipTest('Compressed export requires zstd or Python with compression.zstd')
            compressed.write_bytes(subprocess.run(['zstd', '-q', '-c', str(rollout)],
                                                  capture_output=True, check=True).stdout)
        else:
            compressed.write_bytes(zstd.compress(original))
        rollout.unlink()

        for recorded in (rollout, compressed):
            with self.subTest(recorded_suffix=recorded.suffix):
                with closing(sqlite3.connect(self.source / transfer.STATE)) as con:
                    con.execute('UPDATE threads SET rollout_path=? WHERE id=?', (str(recorded), 'incoming'))
                    con.commit()
                bundle = self.base / (recorded.name + '.zip')
                transfer.make_export(self.source, self.source_project, bundle)
                with zipfile.ZipFile(bundle) as archive:
                    manifest = transfer.validate_bundle(archive)
                    self.assertEqual(set(manifest['files']), {relative})
                    self.assertEqual(archive.read(relative), original)
                target = self.base / (recorded.name + '-home')
                project = self.base / (recorded.name + '-project')
                create_store(target, project, 'existing')
                transfer.apply_import(target, project, bundle, check_closed=False)
                with closing(sqlite3.connect(target / transfer.STATE)) as con:
                    stored_path, = con.execute("SELECT rollout_path FROM threads WHERE id='incoming'").fetchone()
                self.assertEqual(Path(stored_path), target / relative)
                self.assertEqual(json.loads(Path(stored_path).read_bytes())['payload']['cwd'], str(project))

    def test_original_error_survives_windows_cleanup(self):
        original_rows = transfer.rows
        def failing_rows(con, table):
            if table == 'project_roots':
                raise sqlite3.OperationalError('PRIMARY: project metadata cannot be read')
            return original_rows(con, table)
        old_cleanup = tempfile.TemporaryDirectory.cleanup
        tracker = self.tracker
        def cleanup(obj):
            if 'codex-transfer-stage-' in obj.name:
                tracker.clean_stage(obj.name)
            old_cleanup(obj)
        before = {name: (self.target / name).read_bytes() for name in (transfer.STATE, transfer.HISTORY)}
        with mock.patch.object(transfer.sqlite3, 'connect', self.tracker.open), \
             mock.patch.object(transfer, 'rows', failing_rows), \
             mock.patch.object(tempfile.TemporaryDirectory, 'cleanup', cleanup):
            with self.assertRaisesRegex(Exception, 'PRIMARY: project metadata cannot be read'):
                transfer.apply_import(self.target, self.project, self.bundle, check_closed=False)
        self.assertFalse(self.tracker.live, 'SQLite handles survived failed staging')
        self.assertEqual(before, {name: (self.target / name).read_bytes() for name in before})

    def test_existing_foreign_key_defect_does_not_block_additive_merge(self):
        con = sqlite3.connect(self.target / transfer.STATE)
        con.execute("UPDATE threads SET thread_section_id='deleted-sidebar-section' WHERE id='existing'")
        con.commit()
        before = con.execute("SELECT * FROM threads WHERE id='existing'").fetchone()
        con.close()
        transfer.apply_import(self.target, self.project, self.bundle, check_closed=False)
        con = sqlite3.connect(self.target / transfer.STATE)
        try:
            self.assertEqual(before, con.execute("SELECT * FROM threads WHERE id='existing'").fetchone())
            self.assertEqual(2, con.execute('SELECT COUNT(*) FROM threads').fetchone()[0])
        finally:
            con.close()

    def test_locked_cleanup_does_not_hide_primary_error(self):
        real_rmtree = transfer.shutil.rmtree
        def locked_rmtree(path, *args, **kwargs):
            if 'codex-transfer-stage-' in str(path):
                raise NotADirectoryError(267, 'The directory name is invalid', str(Path(path) / transfer.HISTORY))
            return real_rmtree(path, *args, **kwargs)
        with mock.patch.object(transfer.shutil, 'rmtree', locked_rmtree), \
             mock.patch('sys.stderr', new_callable=io.StringIO) as output:
            with self.assertRaisesRegex(RuntimeError, 'Original database error'):
                with transfer.staging_directory(self.base):
                    raise RuntimeError('Original database error')
        self.assertIn('Temporary files remain', output.getvalue())

    def test_new_foreign_key_defect_is_rejected_without_leaking_handles(self):
        real_put = transfer.put_rows
        def put_bad_reference(con, table, data):
            real_put(con, table, data)
            if table == 'thread_dynamic_tools':
                con.execute("INSERT INTO thread_dynamic_tools VALUES ('missing-thread', 0)")
        with mock.patch.object(transfer.sqlite3, 'connect', self.tracker.open), \
             mock.patch.object(transfer, 'put_rows', put_bad_reference):
            with self.assertRaisesRegex(RuntimeError, 'Imported rows introduce invalid database references'):
                transfer.apply_import(self.target, self.project, self.bundle, check_closed=False)
        self.assertFalse(self.tracker.live)
        con = sqlite3.connect(self.target / transfer.STATE)
        try:
            self.assertEqual([('existing',)], con.execute('SELECT id FROM threads').fetchall())
        finally:
            con.close()

    def test_verify_rejects_incorrect_rollout_path(self):
        transfer.apply_import(self.target, self.project, self.bundle, check_closed=False)
        transfer.verify_import(self.target, self.bundle)
        with closing(sqlite3.connect(self.target / transfer.STATE)) as con:
            con.execute("UPDATE threads SET rollout_path='missing-session.jsonl' WHERE id='incoming'")
            con.commit()
        with mock.patch.object(transfer.sqlite3, 'connect', self.tracker.open):
            with self.assertRaisesRegex(RuntimeError, 'State content mismatch: threads'):
                transfer.verify_import(self.target, self.bundle)
        self.assertFalse(self.tracker.live, 'SQLite handles survived failed state verification')

    def test_verify_compares_all_state_tables_after_remapping(self):
        # Include a child chat in a subdirectory and source-only sidebar state.
        child_cwd = self.source_project / 'src'
        child_rollout = self.source / 'sessions' / 'rollout-child.jsonl'
        child_rollout.write_text(json.dumps({'type': 'session_meta', 'payload': {
            'id': 'child', 'cwd': str(child_cwd), 'forked_from_id': 'incoming',
        }}) + '\n', encoding='utf-8')
        with closing(sqlite3.connect(self.source / transfer.STATE)) as con:
            con.execute("INSERT INTO projects VALUES ('source-project', 'Source', '{}', 0, 0, 0)")
            con.execute("INSERT INTO project_roots VALUES ('source-project', 0, ?)", (str(self.source_project),))
            con.execute("INSERT INTO thread_sections VALUES ('source-section')")
            con.execute("UPDATE threads SET project_id='source-project', is_pinned=1, "
                        "thread_section_id='source-section', section_position=3, section_entered_at_ms=42")
            con.execute("INSERT INTO threads VALUES ('child', ?, ?, 'Child chat', 1000, "
                        "'source-project', 1, 'source-section', 4, 43, 'incoming')",
                        (str(child_cwd), str(child_rollout)))
            con.execute("INSERT INTO thread_dynamic_tools VALUES ('incoming', 0)")
            con.execute("INSERT INTO thread_attachments VALUES ('attachment', 'incoming', 'pull_request')")
            con.execute("INSERT INTO thread_spawn_edges VALUES ('incoming', 'child')")
            con.commit()
        bundle = self.base / 'state-history.zip'
        transfer.make_export(self.source, self.source_project, bundle)
        # Reuse a destination project rather than requiring a newly generated ID.
        with closing(sqlite3.connect(self.target / transfer.STATE)) as con:
            con.execute("INSERT INTO projects VALUES ('destination-project', 'Destination', '{}', 0, 0, 0)")
            con.execute("INSERT INTO project_roots VALUES ('destination-project', 0, ?)", (str(self.project),))
            con.commit()
        transfer.apply_import(self.target, self.project, bundle, check_closed=False)
        transfer.verify_import(self.target, bundle)

        mutations = [
            ('threads', 'cwd', str(self.source_project)),
            ('threads', 'project_id', 'source-project'),
            ('threads', 'title', 'Changed title'),
            ('threads', 'is_pinned', 1),
            ('threads', 'thread_section_id', 'source-section'),
            ('threads', 'section_position', 3),
            ('threads', 'section_entered_at_ms', 42),
            ('threads', 'attached_thread_parent_id', 'child'),
            ('thread_dynamic_tools', 'position', 99),
            ('thread_attachments', 'attachment_type', 'file'),
            ('thread_spawn_edges', 'parent_thread_id', 'child'),
        ]
        for table, column, value in mutations:
            with self.subTest(table=table, column=column):
                where = " WHERE id='incoming'" if table == 'threads' else ''
                with closing(sqlite3.connect(self.target / transfer.STATE)) as con:
                    original = con.execute(f'SELECT "{column}" FROM "{table}"' + where).fetchone()[0]
                    con.execute(f'UPDATE "{table}" SET "{column}"=?' + where, (value,))
                    con.commit()
                try:
                    with self.assertRaisesRegex(RuntimeError, 'State content mismatch: ' + table):
                        transfer.verify_import(self.target, bundle)
                finally:
                    with closing(sqlite3.connect(self.target / transfer.STATE)) as con:
                        con.execute(f'UPDATE "{table}" SET "{column}"=?' + where, (original,))
                        con.commit()

    def test_failed_state_verification_rolls_back(self):
        original_prepare = transfer.prepare_merge
        def database_contents():
            # SQLite backup may change file headers while preserving all schema/data.
            contents = {}
            for name in (transfer.STATE, transfer.HISTORY):
                with closing(sqlite3.connect(self.target / name)) as con:
                    contents[name] = list(con.iterdump())
            return contents
        before = database_contents()
        def corrupt_staged_state(*args):
            original_prepare(*args)
            with closing(sqlite3.connect(args[-1] / transfer.STATE)) as con:
                con.execute("UPDATE threads SET rollout_path='missing-session.jsonl' WHERE id='incoming'")
                con.commit()
        with mock.patch.object(transfer.sqlite3, 'connect', self.tracker.open), \
             mock.patch.object(transfer, 'prepare_merge', corrupt_staged_state):
            with self.assertRaisesRegex(RuntimeError, 'State content mismatch: threads'):
                transfer.apply_import(self.target, self.project, self.bundle, check_closed=False)
        self.assertFalse(self.tracker.live)
        self.assertEqual(before, database_contents())
        self.assertFalse((self.target / 'sessions' / 'rollout-incoming.jsonl').exists())
        self.assertFalse((self.target / '.codex-global-state.json').exists())
        self.assertFalse((self.target / 'session_index.jsonl').exists())

    def test_failed_verification_rolls_back_and_closes_database_handles(self):
        original_prepare = transfer.prepare_merge
        original_copy = transfer.shutil.copy2
        def corrupt_staged_history(*args):
            original_prepare(*args)
            con = self.tracker.open(args[-1] / transfer.HISTORY)
            try:
                con.execute("UPDATE thread_items SET item_json='{}' WHERE thread_id='incoming'")
                con.commit()
            finally:
                con.close()
        def windows_copy(source, dest, *args, **kwargs):
            # An outstanding verification connection would prevent rollback on Windows.
            for con in self.tracker.live:
                uri = Path(dest).resolve().as_uri()
                if con.test_path.startswith(uri) or con.test_path == str(dest):
                    raise PermissionError('Database is still open during rollback')
            return original_copy(source, dest, *args, **kwargs)
        with mock.patch.object(transfer.sqlite3, 'connect', self.tracker.open), \
             mock.patch.object(transfer, 'prepare_merge', corrupt_staged_history), \
             mock.patch.object(transfer.shutil, 'copy2', windows_copy):
            with self.assertRaisesRegex(RuntimeError, 'History content mismatch'):
                transfer.apply_import(self.target, self.project, self.bundle, check_closed=False)
        self.assertFalse(self.tracker.live)
        con = sqlite3.connect(self.target / transfer.STATE)
        try:
            self.assertEqual([('existing',)], con.execute('SELECT id FROM threads').fetchall())
        finally:
            con.close()
        con = sqlite3.connect(self.target / transfer.HISTORY)
        try:
            self.assertEqual([('existing',)], con.execute('SELECT thread_id FROM thread_items').fetchall())
        finally:
            con.close()

if __name__ == '__main__':
    unittest.main()
