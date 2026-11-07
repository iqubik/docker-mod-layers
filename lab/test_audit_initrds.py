"""Небольшая проверка охвата ссылок и распознавания точного дефекта."""
import importlib.util
import pathlib
import tempfile

spec = importlib.util.spec_from_file_location('audit',pathlib.Path(__file__).with_name('audit-initrds.py'))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
with tempfile.TemporaryDirectory() as work:
    directory=pathlib.Path(work)
    (directory/'menu.ipxe').write_text('''#!ipxe
item pop Pop 22.04
:pop
set kernel_url ${live_endpoint}/ubuntu-squash/releases/download/21-tag/
kernel ${kernel_url}vmlinuz boot=casper
initrd ${kernel_url}initrd
''')
    rows=audit.inventory(directory)
    assert len(rows)==1
    assert rows[0]['url']=='https://github.com/netbootxyz/ubuntu-squash/releases/download/21-tag/initrd'
    assert rows[0]['labels']==['Pop 22.04']
bad='do_urlmount() {\n mount -t tmpfs -o size=`/bin/curl -sI ${URL} | sed -ne \'/Content-Length/{s/.*: //;p}\'` tmpfs ${mountpoint}\n}\n'
assert audit.classify(bad)[0]=='подтверждён дефект size'
assert audit.classify('nothing')[0]=='нет URL/HTTP mount-функции'
assert audit.classify(bad.replace('do_urlmount','do_httpmount'))[0]=='подтверждён дефект size'
assert audit.classify(bad.replace('curl -sI','curl -sLI'))[0].startswith('иная')
print('Проверки пройдены')
