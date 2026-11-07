#!/bin/sh
# Одноразовый контейнер на ПК: распаковываем только casper, не всё содержимое ОС.
set -eu
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq initramfs-tools-core cpio zstd lz4 xz-utils >/tmp/audit-packages.log 2>&1
cat > /usr/local/bin/cpio <<'EOF'
#!/bin/sh
exec /bin/cpio "$@" -d 'scripts/casper' './scripts/casper'
EOF
chmod 755 /usr/local/bin/cpio
