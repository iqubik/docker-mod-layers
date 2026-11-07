# Supplemental local investigation, 2026-11-07

This branch archives the bug-related audit/rebuild tools separately from the
production PR, keeping the reviewable change small. Public artifact URLs and
hashes are included; there are no binary images or home-server deployment
configuration files.

## What is included

- `audit-2026-11-07/results.json`: sanitized 52-artifact audit, including the
  extracted defective functions and source SHA256.
- `audit-2026-11-07/targets.json`: exact public download targets. The KDE Neon
  target uses its actual `initrd.lz` asset; its menu incorrectly requested `initrd`.
- `audit-initrds.py` and `audit-initrds-container.sh`: download/decompress and
  inspect the files in an isolated container, retaining checksums and function text.
- `casper-urlmount.sh`: the **exact earlier experimental workaround**, not the
  proposed generic production function. It forces IPv4, checks an additional
  512 MiB RAM reserve, and adds 16 MiB tmpfs headroom. Its original diagnostic
  prefix/comments are retained to preserve the recorded source hashes.
- `patch-popos-initrd.py`: the guarded function replacement, also supporting
  older `do_httpmount()` implementations. Its standalone CLI is pinned to the
  specific Pop!_OS input originally investigated.
- `rebuild-fixed-initrds.py`: verify each original hash, decompress, change exactly
  one newc/cpio record, verify all remaining records including metadata, recompress
  with the original codec/check type, decompress again and verify the result.
- `fixed-2026-11-07/builds.json`: local output SHA256 and per-archive integrity
  results for 46 rebuilds. These are **local experimental outputs**, not builds
  of the generic function proposed by the PR.
- `fixed-2026-11-07/kernels.json`: public matching-kernel URLs, sizes and SHA256.
- `environment.json`: container digest and compressor/Python versions.
- `test_*.py`: the original focused tests for the audit, cpio replacement and
  experimental download function.

The CSV summaries and English explanation are in `../docs/`. Exactly 46 cpio
records changed; 170029 other records remained byte-identical. One rebuilt
Pop!_OS 22.04 Intel/AMD image was reported to boot successfully on the tester's
legacy-BIOS Toshiba laptop. Other outputs were checked structurally, not booted.

The original host was Windows with Python and Docker Desktop; the audit driver
calls `curl.exe`. The cpio rebuilder itself runs inside Linux. This is an archival
reproduction workflow, not a new deployment framework. Allow roughly 10 GiB of
temporary disk space for downloads, output archives and scratch data.

## Repeat the artifact audit (PowerShell)

Run from this repository's root:

```powershell
$auditWork = Join-Path $env:TEMP 'netboot-audit-20261107'
New-Item -ItemType Directory -Force -Path $auditWork | Out-Null
Copy-Item lab/audit-2026-11-07/targets.json -Destination $auditWork
docker run -d --name netboot-initrd-audit-20261107 --cpus 2 --memory 1536m -v "${auditWork}:/work" debian@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241 sleep infinity
Get-Content -Raw lab/audit-initrds-container.sh | docker exec -i netboot-initrd-audit-20261107 sh -c "tr -d '\r' | sh"
python lab/test_audit_initrds.py
python lab/audit-initrds.py $auditWork
docker rm -f netboot-initrd-audit-20261107
```

The existing three-part URL limit is retained in the experimental workaround,
as it is in the production proposal. The audit uses exact released initrd files;
it does not infer a released artifact's contents merely from a current Dockerfile.

## Repeat the local experimental rebuilds

After the downloads above, from the repository root in PowerShell:

```powershell
$fixedWork = Join-Path $env:TEMP 'netboot-fixed-20261107'
New-Item -ItemType Directory -Force -Path $fixedWork | Out-Null
docker run --rm --cpus 2 --memory 1536m -v "${auditWork}:/originals:ro" -v "${fixedWork}:/work" -v "${PWD}/lab:/scripts:ro" debian@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241 sh -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 zstd xz-utils gzip && python3 /scripts/rebuild-fixed-initrds.py /originals /work'
python lab/test_rebuild_fixed_initrds.py
```

The rebuild refuses inputs whose SHA256 differs from the audit. Existing validated
outputs can be reused. Compressor version changes may change compressed-file
hashes; the decompressed contents and unchanged cpio records are checked regardless.
None of these commands modifies a router, DHCP configuration or running server.
