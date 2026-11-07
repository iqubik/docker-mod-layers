"""Создать scripts/casper для точного исходного Pop!_OS initrd, не менять оригинал."""
import hashlib
import pathlib
import re
import sys

INITRD_SHA = 'cfb03a91d01dca23fcd5f03774d218307139169351bc496c412f78c551d92b27'


def patch(content, replacement, name='do_urlmount'):
    assert name in ('do_urlmount', 'do_httpmount')
    pattern = rf'(?ms)^{name}\(\) \{{\n.*?^\}}'
    matches = list(re.finditer(pattern, content))
    assert len(matches) == 1, 'Неожиданный casper: нужна одна ' + name
    original = matches[0].group()
    assert 'mount -t tmpfs -o size=`/bin/curl -sI ${URL}' in original
    assert '${URL}.part2' in original
    replacement = replacement.replace('do_urlmount() {', name + '() {', 1)
    return content[:matches[0].start()] + replacement.rstrip() + content[matches[0].end():]


if __name__ == '__main__':
    directory = pathlib.Path(sys.argv[1])
    assert hashlib.sha256((directory / 'initrd').read_bytes()).hexdigest() == INITRD_SHA
    original = (directory / 'casper').read_text()
    replacement = pathlib.Path(__file__).with_name('casper-urlmount.sh').read_text()
    (directory / 'casper.fixed').write_text(patch(original, replacement), newline='\n')
    print('Подготовлен casper.fixed; исходный initrd не изменён.')
