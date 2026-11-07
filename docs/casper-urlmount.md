# Casper URL mounting: sizing and multipart failures

The modification layer's `root/patch` replaces Ubuntu's `do_urlmount()` to load
a squashfs image into tmpfs. Previously it sized tmpfs using:

```sh
mount -t tmpfs -o size=`/bin/curl -sI ${URL} | sed -ne '/Content-Length/{s/.*: //;p}'` tmpfs ${mountpoint}
```

This has several independent failure modes:

1. HEAD does not follow redirects. GitHub release URLs can return 302 with
   `Content-Length: 0`; the actual asset's length is only available after following
   the redirect. **`size=0` is not a zero-byte filesystem: Linux treats it as no
   tmpfs size limit.** That can hide the bug while removing the intended bound.
2. HTTP field names are case-insensitive. For example, HTTP/2 header output may
   contain `content-length`, which the case-sensitive sed expression misses.
   Empty `size=` is rejected by mount.
3. Only the first asset contributes to the size, although `.part2` / `.part3`
   may be appended to the same file.
4. A failed mount does not prevent downloading into the underlying initramfs.
   A failed later GET does not undo the earlier `rc=0` success result.
5. GET lacks `--fail`; an HTTP error page can be appended to the squashfs.
   Checking an optional part's redirect status also does not establish that the
   final target exists.

Observed Pop!_OS symptoms included `tmpfs: Bad value for 'size'`, a failed mount
on `/cdrom`, `curl: (23) Failure writing output to destination`, and subsequently
`Unable to find a live file system on the network`.

## Fix

The patched function follows HEAD redirects, uses the final HTTP response and
case-insensitive Content-Length, requires a positive numeric size, and sums all
available parts before mounting. Optional 404 stops probing; other HTTP/transport
errors fail before mounting. Downloads use `--fail` and their combined byte count
must match the HEAD total. Mount/download failures return failure; a failed image
is removed and the new tmpfs is unmounted.

Shell positional parameters retain the validated URLs without word splitting or
globbing. Mount paths and URLs are quoted. Appended downloads are not retried,
because retrying an append can duplicate bytes already written by a failed GET.

The existing maximum of three parts is unchanged. Expanding that limit is tracked
separately in [PR #91](https://github.com/netbootxyz/docker-mod-layers/pull/91).
TLS verification and IPv4/IPv6 selection retain curl's normal behavior. No
machine-specific RAM reserve is imposed; the live filesystem and running OS must
still fit in the client's memory.

## Regression tests

The tests extract pristine casper from Ubuntu's actual package, apply **this
repository's patch with `--fuzz=0`**, and run the resulting function with real curl
against an HTTP server on loopback. Only mount/umount are mocked, so the suite does
not require root privileges or a real tmpfs mount.

Fourteen scenarios run with both dash and BusyBox ash: redirects and mixed-case
headers, one/two/three-part images, mount/file-creation failures, HTTP/transport failures, missing
or invalid lengths, truncated GET, and disagreement between HEAD and GET sizes.
The mountpoint deliberately contains whitespace.

Validated against pristine packages from Ubuntu **20.04, 22.04 and 24.04**: all
**84 scenario/shell/Ubuntu combinations passed**. The unmodified layer patch fails
six baseline checks covering redirect sizing, failed mount, and failed later part
on both shells. The patch uses two lines of context so Noble's additional adjacent
`iso-url` case does not require patch fuzz.

For example, in an Ubuntu container with python3/curl/patch/busybox installed:

```sh
mkdir /tmp/casper-source
cd /tmp/casper-source
apt-get download casper
dpkg-deb -x casper*.deb unpacked
cd /path/to/this/repository
CASPER_SOURCE=/tmp/casper-source/unpacked/usr/share/initramfs-tools/scripts/casper \
    python3 tests/test_casper_urlmount.py
```

The GitHub Actions workflow runs the same Ubuntu matrix without registry
credentials. `CASPER_PATCH` can select a baseline patch for regression comparisons.

## Released-artifact evidence

[initrd-audit-2026-11-07.csv](initrd-audit-2026-11-07.csv) records the exact public
URLs, sizes and SHA256 of **52 downloaded, decompressed initrd artifacts** referenced
by the installed netboot.xyz 3.0.3 casper-related menus, including neighboring live-boot entries.
This is not a claim that every distribution supported by netboot.xyz was audited.

- **46** contained the faulty expression: 39 in `do_urlmount()`, seven older
  artifacts in `do_httpmount()`.
- Five contained no `scripts/casper`.
- One (Ubuntu Spins) used a different URL-mount implementation without this defect.

The pattern appears in Pop!_OS, Ubuntu flavors, Mint's Ubuntu editions, elementary
OS, Bodhi, BackBox, Kodachi, Linux Lite, LXLE, KDE Neon, Nitrux, Regolith, Voyager's
Ubuntu editions, Zorin, Rescuezilla, Boot Repair CD, and CAINE. Static detection
does not establish that all 46 fail on all machines/transports.

[local-rebuilds-2026-11-07.csv](local-rebuilds-2026-11-07.csv) records an earlier
local experimental workaround applied to all 46 artifacts. Exactly one cpio
record per archive changed; **170029 other records** remained byte-identical,
including metadata. Original compression formats were retained, and each output
was decompressed again to verify its content. One rebuilt Pop!_OS 22.04 Intel/AMD
image successfully booted on a legacy-BIOS Toshiba laptop.

**Those local output hashes are not hashes of images built with this exact PR.**
The earlier workaround also forced IPv4, used a 512 MiB available-memory reserve
and allowed 16 MiB tmpfs headroom. These local policy choices are deliberately
absent here. Other rebuilt images were checked structurally, not boot-tested.
The proposed generic function has not yet been boot-tested on hardware, and a
full static-curl/multistage Docker build has not been run for this change.

## Rollout

Merging the source fix alone cannot update already published initrds. Rebuild
the affected mod-layer images, then rebuild their downstream distro artifacts
and update the menu endpoints to the new release assets.

At inspection time the same faulty expression existed in the Ubuntu 18.04,
20.04, 22.04, 23.04, 23.10, 24.04 and 25.04 branches. The 20.04 through 25.04
`root/patch` files were identical; the 18.04 hunk differs and needs a separate
backport. This PR targets the 22.04 branch; the source change is also tested
against the current 20.04 and 24.04 packages.

Related reports:
[netboot.xyz #1399](https://github.com/netbootxyz/netboot.xyz/issues/1399),
[netboot.xyz #1588](https://github.com/netbootxyz/netboot.xyz/issues/1588).
These reports can also involve memory capacity or the number of parts; this
change does not claim to resolve every cause described there.
