import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent


class Tests(unittest.TestCase):
    def run_case(self, case):
        with tempfile.TemporaryDirectory(prefix='casper-test-') as directory:
            path = pathlib.Path(directory)
            (path / 'meminfo').write_text('MemAvailable: ' + ('1' if case == 'lowram' else '4194304') + ' kB\n')
            function = (ROOT / 'casper-urlmount.sh').read_text().replace('/bin/curl', 'mock_curl').replace('/proc/meminfo', '/test/meminfo')
            harness = '''#!/bin/sh
mock_curl() {
    for address do :; done
    case "$*" in
      *-sSLI*)
        case "$address" in
          *.part3) printf 'HTTP/2 404\\r\\ncontent-length: 9\\r\\n\\n404' ;;
          *.part2) printf 'HTTP/2 200\\r\\ncontent-length: 3\\r\\n\\n200' ;;
          *) if [ "$CASE" = badhead ]; then
               printf 'HTTP/2 200\\r\\n\\n200'
             else
               printf 'HTTP/2 302\\r\\nContent-Length: 0\\r\\n\\r\\nHTTP/2 200\\r\\ncontent-length: 4\\r\\n\\n200'
             fi ;;
        esac ;;
      *) echo GET >> /test/requests
         case "$address" in
           *.part2) [ "$CASE" != partfail ] || return 22; printf efg ;;
           *) printf abcd ;;
         esac ;;
    esac
}
mount() { echo "$*" > /test/mountargs; [ "$CASE" != mountfail ]; }
umount() { echo unmounted > /test/unmounted; }
'''
            harness += function + '\nURL=https://example.invalid/image\nmountpoint=/test/cdrom\ndo_urlmount\n'
            (path / 'test.sh').write_text(harness, newline='\n')
            result = subprocess.run(['docker','run','--rm','--cpus','1','--memory','256m',
                '-e','CASE='+case,'-v',str(path)+':/test','golang:1.26.4-alpine',
                'sh','/test/test.sh'],capture_output=True,text=True,timeout=25)
            if case == 'success':
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((path / 'cdrom/casper/root.squashfs').read_bytes(), b'abcdefg')
                self.assertIn('size=16777223', (path / 'mountargs').read_text())
            else:
                self.assertNotEqual(result.returncode, 0, case)
                self.assertFalse((path / 'cdrom/casper/root.squashfs').exists())
                if case in ('mountfail','badhead','lowram'):
                    self.assertFalse((path / 'requests').exists())
                if case == 'partfail':
                    self.assertTrue((path / 'unmounted').exists())

    def test_cases(self):
        for case in ('success','badhead','lowram','mountfail','partfail'):
            with self.subTest(case=case):
                self.run_case(case)


if __name__ == '__main__':
    unittest.main()
