"""RO-аудит готовых initrd из снимка установленных меню; загрузка и распаковка на ПК."""
import argparse
import concurrent.futures
import hashlib
import json
import pathlib
import re
import subprocess
import threading

CONTAINER = 'netboot-initrd-audit-20261107'
LOCK = threading.Lock()


def inventory(directory):
    rows = {}
    for file in sorted(directory.glob('*.ipxe')):
        text = file.read_text()
        if 'boot=casper' not in text:
            continue
        os_match = re.search(r'^set os (.+)$', text, re.M)
        os_name = os_match.group(1) if os_match else file.stem
        names = dict(re.findall(r'^item\s+(\S+)\s+(.+)$', text, re.M))
        label = ''
        for line in text.splitlines():
            if line.startswith(':'):
                label = line[1:]
            for path in re.findall(r'\$\{live_endpoint\}/([\w-]+/releases/download/[^/\s]+/)(?:initrd|(?=\s|$))', line):
                url = 'https://github.com/netbootxyz/' + path + 'initrd'
                row = rows.setdefault(url, {'url': url, 'menus': [], 'labels': []})
                if file.name not in row['menus']:
                    row['menus'].append(file.name)
                name = names.get(label, label).replace('${space} ', '').replace('${os}', os_name)
                if name not in row['labels']:
                    row['labels'].append(name)
    return list(rows.values())


def classify(text):
    functions = re.findall(r'(?ms)^\w+\(\)\s*\{.*?^\}', text)
    bad = [function for function in functions if any(
        'mount ' in line and 'size=' in line and 'curl -sI' in line
        and '/Content-Length/' in line for line in function.splitlines())]
    if bad:
        return 'подтверждён дефект size', '\n\n'.join(bad)
    selected = [f for f in functions if f.startswith(('do_urlmount(', 'do_httpmount('))]
    return ('иная реализация — см. функцию' if selected else 'нет URL/HTTP mount-функции'), '\n\n'.join(selected)


def audit(row, directory):
    key = hashlib.sha256(row['url'].encode()).hexdigest()[:16]
    binary = directory / (key + '.initrd')
    result = dict(row)
    try:
        if not binary.exists():
            part = binary.with_suffix('.part')
            run = subprocess.run(['curl.exe', '-4', '-fLsS', '--connect-timeout', '15',
                '--max-time', '300', '--retry', '1', '--limit-rate', '10M',
                row['url'], '-o', str(part)], capture_output=True, timeout=630)
            if run.returncode:
                raise RuntimeError(run.stderr.decode(errors='replace').strip())
            assert part.stat().st_size > 1024, 'Ответ слишком мал для initrd'
            part.replace(binary)
        digest = hashlib.sha256()
        with binary.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        result.update(sha256=digest.hexdigest(), bytes=binary.stat().st_size)
        # Одновременно распаковываем только один архив: ограничиваем RAM и место.
        with LOCK:
            command = ('set -eu; mkdir -p /tmp/initrd-audit; '
                'unmkinitramfs /work/' + binary.name + ' /tmp/initrd-audit/' + key + ' >/tmp/unmk.log 2>&1; '
                'find /tmp/initrd-audit/' + key + ' -type f -path "*/scripts/casper" -print')
            run = subprocess.run(['docker', 'exec', CONTAINER, 'sh', '-c', command],
                                 capture_output=True, timeout=180)
            if run.returncode:
                log = subprocess.run(['docker','exec',CONTAINER,'cat','/tmp/unmk.log'],capture_output=True)
                raise RuntimeError('unmkinitramfs: ' + log.stdout.decode(errors='replace')[-1000:])
            paths = run.stdout.decode().splitlines()
            result['casper_files'] = paths
            if not paths:
                listing = subprocess.run(['docker','exec',CONTAINER,'sh','-c',
                    'PATH=/usr/bin:/bin lsinitramfs /work/' + binary.name],
                    capture_output=True,check=True,timeout=180).stdout.decode().splitlines()
                assert listing, 'Не удалось прочитать содержимое архива'
                assert not any(x.rstrip('/').endswith('scripts/casper') for x in listing), 'casper пропущен при распаковке'
                result['archive_entries'] = len(listing)
                result['live_scripts'] = [x for x in listing if x.rstrip('/').endswith('scripts/live')]
                result['status'] = 'нет scripts/casper'
            else:
                assert len(paths) == 1, 'Найдено несколько casper — требуется разбор'
                content = subprocess.run(['docker','exec',CONTAINER,'cat',paths[0]],
                                         capture_output=True,check=True).stdout
                (directory / (key + '.casper')).write_bytes(content)
                result['casper_sha256'] = hashlib.sha256(content).hexdigest()
                result['status'], function = classify(content.decode(errors='replace'))
                result['function'] = function
            subprocess.run(['docker','exec',CONTAINER,'rm','-rf','/tmp/initrd-audit/'+key],check=True)
    except Exception as error:
        result['status'] = 'НЕ ПРОВЕРЕН: ' + str(error)
    with LOCK:
        (directory / (key + '.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(result['status'], row['url'], flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=pathlib.Path)
    args = parser.parse_args()
    targets = args.directory / 'targets.json'
    rows = json.loads(targets.read_text(encoding='utf-8')) if targets.exists() else inventory(args.directory)
    assert len(rows) > 40, 'Неполный снимок меню'
    assert all(re.fullmatch(r'https://github\.com/netbootxyz/[\w-]+/releases/download/[\w.-]+/initrd[\w.-]*',r['url']) for r in rows), 'Неожиданный источник initrd'
    print('Уникальных initrd:', len(rows), flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda row: audit(row,args.directory), rows))
    (args.directory/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Готово:',len(results),flush=True)


if __name__ == '__main__':
    main()
