"""Apply the real layer patch to Ubuntu's casper and exercise it with real curl."""
import contextlib
import http.server
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]


class Handler(http.server.BaseHTTPRequestHandler):
    def respond(self, head):
        self.server.requests.append((self.command, self.path))
        name = urllib.parse.unquote(self.path.rsplit('/', 1)[-1])
        if self.path.startswith('/redirect/'):
            self.send_response(302)
            self.send_header('Location', '/files/' + urllib.parse.quote(name))
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        case = self.server.case
        payloads = {'image file': b'abcd', 'image file.part2': b'efg'}
        if case == 'single':
            payloads.pop('image file.part2')
        if case == 'third':
            payloads['image file.part3'] = b'hij'
        payload = payloads.get(name)
        status = 200 if payload is not None else 404
        if head and case == 'missing_main' and name == 'image file':
            status = 404
        if head and case == 'head_error' and name.endswith('.part2'):
            status = 503
        if not head and case == 'get_error' and name.endswith('.part2'):
            status = 503
        if head and case == 'transport_error' and name == 'image file':
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        body = payload if status == 200 else b'error page'
        length = str(len(body))
        if head and name == 'image file':
            if case == 'missing_length':
                length = None
            elif case == 'zero_length':
                length = '0'
            elif case == 'invalid_length':
                length = 'invalid'
        if not head and name.endswith('.part2'):
            if case == 'changed_length':
                body += b'x'
                length = str(len(body))
            elif case == 'short_get':
                body = body[:-1]
        self.send_response(status)
        if length is not None:
            self.send_header('cOnTeNt-LeNgTh', length)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_HEAD(self):
        self.respond(True)

    def do_GET(self):
        self.respond(False)

    def log_message(self, *_):
        pass


@contextlib.contextmanager
def endpoint(case):
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.case, server.requests = case, []
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    try:
        yield server, f'http://127.0.0.1:{server.server_port}/redirect/image%20file'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class URLMountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.workspace.cleanup)
        source = Path(os.environ.get('CASPER_SOURCE', '/usr/share/initramfs-tools/scripts/casper'))
        target = Path(cls.workspace.name) / 'casper'
        shutil.copyfile(source, target)
        patch = os.environ.get('CASPER_PATCH', str(ROOT / 'root/patch'))
        result = subprocess.run(['patch', '--batch', '--fuzz=0', str(target), '-i', patch],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        subprocess.run(['sh', '-n', str(target)], check=True)
        match = re.search(r'(?ms)^do_urlmount\(\) \{\n.*?^\}', target.read_text())
        assert match, result.stdout
        cls.function = match.group()

    def run_case(self, case):
        shells = [['/bin/sh']]
        if shutil.which('busybox'):
            shells.append(['busybox', 'ash'])
        for shell in shells:
            with self.subTest(shell=shell, case=case), tempfile.TemporaryDirectory() as directory, endpoint(case) as (server, url):
                work = Path(directory)
                mountpoint = work / 'mount point'
                script = work / 'run.sh'
                script.write_text('''mount() {
    printf '%s\\n' "$@" > "$TEST_DIR/mount-args"
    if [ "$CASE" = file_create_error ]; then
        command mkdir -p "$mountpoint/casper"
        ln -s "$TEST_DIR/missing/target" "$mountpoint/casper/root.squashfs"
    fi
    [ "$CASE" != mount_error ]
}
umount() { printf unmounted > "$TEST_DIR/unmounted"; }
''' + self.function + '\ndo_urlmount\n')
                env = dict(os.environ, URL=url, mountpoint=str(mountpoint), TEST_DIR=str(work), CASE=case)
                result = subprocess.run(shell + [str(script)], env=env, capture_output=True, timeout=20)
                image = mountpoint / 'casper/root.squashfs'
                success = case in ('redirect', 'single', 'third')
                gets = [path for method, path in server.requests if method == 'GET']
                if success:
                    self.assertEqual(result.returncode, 0, result.stderr.decode())
                    expected = {'single': b'abcd', 'redirect': b'abcdefg', 'third': b'abcdefghij'}[case]
                    self.assertEqual(image.read_bytes(), expected)
                    self.assertEqual((work / 'mount-args').read_text().splitlines(),
                                     ['-t', 'tmpfs', '-o', f'size={len(expected)}', 'tmpfs', str(mountpoint)])
                else:
                    self.assertNotEqual(result.returncode, 0, result.stderr.decode())
                    self.assertFalse(image.exists())
                    if case in ('get_error', 'short_get', 'changed_length', 'file_create_error'):
                        self.assertTrue((work / 'unmounted').exists())
                        self.assertFalse(image.is_symlink())
                    else:
                        self.assertEqual(gets, [])
                if case in ('missing_main', 'head_error', 'missing_length', 'zero_length', 'invalid_length', 'transport_error'):
                    self.assertFalse((work / 'mount-args').exists())

    def test_redirect_sizing(self):
        self.run_case('redirect')

    def test_single_part(self):
        self.run_case('single')

    def test_three_parts(self):
        self.run_case('third')

    def test_failed_mount(self):
        self.run_case('mount_error')

    def test_failed_part(self):
        self.run_case('get_error')

    def test_file_creation_error(self):
        self.run_case('file_create_error')

    def test_short_get(self):
        self.run_case('short_get')

    def test_changed_length(self):
        self.run_case('changed_length')

    def test_missing_main(self):
        self.run_case('missing_main')

    def test_head_error(self):
        self.run_case('head_error')

    def test_missing_length(self):
        self.run_case('missing_length')

    def test_zero_length(self):
        self.run_case('zero_length')

    def test_invalid_length(self):
        self.run_case('invalid_length')

    def test_transport_error(self):
        self.run_case('transport_error')


if __name__ == '__main__':
    unittest.main()
