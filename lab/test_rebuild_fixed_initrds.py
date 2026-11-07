"""Проверка изменения одной cpio-записи и обоих вариантов casper."""
import importlib.util
import pathlib
import tempfile

spec=importlib.util.spec_from_file_location('builder',pathlib.Path(__file__).with_name('rebuild-fixed-initrds.py'))
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


def entry(name,body,mode=0o100644):
    name=name.encode()+b'\0'
    fields=[1,mode,123,456,1,12345678,len(body),0,0,0,0,len(name),0]
    header=b'070701'+b''.join(f'{n:08x}'.encode() for n in fields)
    prefix=header+name+b'\0'*((-(110+len(name)))%4)
    return prefix+body+b'\0'*((-len(body))%4)


for function in ('do_urlmount','do_httpmount'):
    with tempfile.TemporaryDirectory() as directory:
        path=pathlib.Path(directory)
        source=path/'original';target=path/'fixed'
        casper=(function+'() {\n    mount -t tmpfs -o size=`/bin/curl -sI ${URL} | sed -ne \'/Content-Length/{s/.*: //;p}\'` tmpfs ${mountpoint}\n    curl ${URL}.part2\n}\n').encode()
        data=entry('bin/sh',b'busybox',0o120777)+entry('scripts/casper',casper)+entry('other',b'unchanged')+entry('TRAILER!!!',b'')+b'\0'*20
        source.write_bytes(data)
        replacement,checks=builder.patch_archive(source,target,function)
        assert replacement.startswith((function+'() {').encode())
        assert b'RK fix:' in replacement and b'/Content-Length/' not in replacement
        assert checks['records']==4 and checks['unchanged_records']==3
        assert source.read_bytes()==data
        assert len(list(builder.records(target.read_bytes())))==4
try:
    list(builder.records(b'broken'))
    raise AssertionError('Повреждённый архив принят')
except AssertionError as error:
    assert str(error)=='Неизвестный cpio'
print('Проверены cpio, метаданные, сохранность остальных записей и оба имени функции')
