"""Пересобрать подтверждённые initrd на ПК, меняя только запись scripts/casper."""
import hashlib
import importlib.util
import json
import mmap
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('patcher', ROOT/'patch-popos-initrd.py')
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def records(data):
    position = 0
    while position < len(data):
        while position < len(data) and data[position] == 0:
            position += 1
        if position == len(data):
            return
        start = position
        header = data[start:start+110]
        assert len(header) == 110 and header[:6] in (b'070701', b'070702'), 'Неизвестный cpio'
        fields = [int(header[6+i*8:14+i*8],16) for i in range(13)]
        size, namesize = fields[6], fields[11]
        assert 0 < namesize < 65536
        name_end = start+110+namesize
        assert name_end <= len(data) and data[name_end-1] == 0
        name = data[start+110:name_end-1].decode('utf-8')
        body = (name_end+3)&~3
        end = (body+size+3)&~3
        assert end <= len(data), 'Обрезанная запись cpio'
        yield {'name':name,'start':start,'body':body,'end':end,'size':size,
               'header':header,'fields':fields,'record_sha':hashlib.sha256(data[start:end]).hexdigest()}
        position=end


def patch_archive(source, destination, function):
    with source.open('rb') as stream, mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as data:
        before = list(records(data))
        targets = [r for r in before if r['name'].removeprefix('./')=='scripts/casper']
        assert len(targets)==1
        target=targets[0]
        original=data[target['body']:target['body']+target['size']]
        replacement=patcher.patch(original.decode(),(ROOT/'casper-urlmount.sh').read_text(),function).encode()
        header=bytearray(target['header'])
        header[54:62]=f'{len(replacement):08x}'.encode()
        if header[:6]==b'070702':
            header[102:110]=f'{sum(replacement)&0xffffffff:08x}'.encode()
        with destination.open('wb') as out:
            for offset in range(0,target['start'],1024*1024):
                out.write(data[offset:min(offset+1024*1024,target['start'])])
            out.write(header)
            out.write(data[target['start']+110:target['body']])
            out.write(replacement);out.write(b'\0'*((-len(replacement))%4))
            for offset in range(target['end'],len(data),1024*1024):
                out.write(data[offset:offset+1024*1024])
        # Проверяем наличие всех новых команд в исходном initramfs.
        paths={r['name'].removeprefix('./') for r in before}
        commands={name:sorted(p for p in paths if p in (name,'bin/'+name,'sbin/'+name,'usr/bin/'+name,'usr/sbin/'+name))
                  for name in ('awk','tail','wc','tr','mount','mkdir','umount','rm','sh','curl')}
    with destination.open('rb') as stream, mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as data:
        after=list(records(data))
    assert len(before)==len(after)
    for old,new in zip(before,after):
        assert old['name']==new['name']
        if old is target:
            fields=list(old['fields']);fields[6]=new['fields'][6];fields[12]=new['fields'][12]
            assert fields==new['fields']
        else:
            assert old['record_sha']==new['record_sha'], 'Постороннее изменение: '+old['name']
    return replacement, {'records':len(before),'unchanged_records':len(before)-1,
        'casper_original_sha256':hashlib.sha256(original).hexdigest(),
        'casper_fixed_sha256':hashlib.sha256(replacement).hexdigest(), 'command_paths':commands}


def codec(path):
    with path.open('rb') as stream:
        magic=stream.read(12)
    if magic.startswith(bytes.fromhex('28b52ffd')):
        return 'zstd',['zstd','-q','-d','-c'],['zstd','-q','-T2','-3','-c']
    if magic.startswith(b'\x1f\x8b'):
        return 'gzip',['gzip','-d','-c'],['gzip','-n','-1','-c']
    assert magic.startswith(b'\xfd7zXZ\0'), 'Неизвестное сжатие'
    check={1:'crc32',4:'crc64',10:'sha256'}[magic[7]&15]
    return 'xz-'+check,['xz','-d','-c'],['xz','--check='+check,'-T1','-1','-c']


def convert(command, source, target):
    with source.open('rb') as inp,target.open('wb') as out:
        subprocess.run(command,stdin=inp,stdout=out,check=True,timeout=300)


def build(row, originals, output):
    key=hashlib.sha256(row['url'].encode()).hexdigest()[:16]
    source=originals/(key+'.initrd')
    assert sha(source)==row['sha256'], 'Другой исходный initrd'
    target=output/key
    target.mkdir(exist_ok=True)
    manifest=target/'build.json'
    if manifest.exists():
        existing=json.loads(manifest.read_text())
        assert existing['source_sha256']==row['sha256']
        assert existing['fix_sha256']==sha(ROOT/'casper-urlmount.sh')
        assert existing['patcher_sha256']==sha(ROOT/'patch-popos-initrd.py')
        assert existing['initrd_sha256']==sha(target/'initrd')
        return existing
    function=row['function'].split('(')[0]
    kind,decode,encode=codec(source)
    with tempfile.TemporaryDirectory(prefix='initrd-build-') as scratch:
        work=pathlib.Path(scratch)
        raw=work/'original.cpio';fixed=work/'fixed.cpio';roundtrip=work/'roundtrip.cpio'
        convert(decode,source,raw)
        replacement,checks=patch_archive(raw,fixed,function)
        assert all(checks['command_paths'].values()), 'Нет требуемых команд: '+str(checks['command_paths'])
        casper=target/'casper.fixed';casper.write_bytes(replacement)
        subprocess.run(['sh','-n',str(casper)],check=True)
        temporary=target/'initrd.part'
        convert(encode,fixed,temporary)
        convert(decode,temporary,roundtrip)
        assert sha(roundtrip)==sha(fixed),'Содержимое изменилось при сжатии'
        temporary.replace(target/'initrd')
    result={'key':key,'url':row['url'],'labels':row['labels'],'menus':row['menus'],
        'source_sha256':row['sha256'],'compression':kind,'mount_function':function,
        'fix_sha256':sha(ROOT/'casper-urlmount.sh'),'patcher_sha256':sha(ROOT/'patch-popos-initrd.py'),
        'initrd_sha256':sha(target/'initrd'),'initrd_bytes':(target/'initrd').stat().st_size,
        **checks}
    manifest.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


def main():
    originals,output=map(pathlib.Path,sys.argv[1:])
    output.mkdir(exist_ok=True)
    rows=[r for r in json.loads((ROOT/'audit-2026-11-07/results.json').read_text())
          if r['status']=='подтверждён дефект size']
    assert len(rows)==46
    built=[]
    for index,row in enumerate(rows,1):
        result=build(row,originals,output);built.append(result)
        print(f'{index}/46 {result["key"]} {result["compression"]}: '+ '; '.join(result['labels']),flush=True)
    (output/'builds.json').write_text(json.dumps(built,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
