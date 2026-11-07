do_urlmount() {
    local total urls candidate headers status size available needed limit image actual
    # HEAD должен учитывать редиректы, регистр заголовков и все части squashfs.
    total=0
    urls=""
    for candidate in "${URL}" "${URL}.part2" "${URL}.part3"; do
        headers=$(/bin/curl -4 -sSLI --connect-timeout 10 --max-time 45 \
            -w '\n%{http_code}' "${candidate}") || {
            echo "RK fix: HEAD failed: ${candidate}" >&2
            return 1
        }
        status=$(printf '%s\n' "${headers}" | tail -n 1)
        if [ "${status}" = 404 ] && [ "${candidate}" != "${URL}" ]; then
            break
        fi
        [ "${status}" = 200 ] || {
            echo "RK fix: HTTP ${status} for ${candidate}" >&2
            return 1
        }
        size=$(printf '%s\n' "${headers}" | awk '
            /^HTTP\// { n="" }
            tolower($1)=="content-length:" { gsub("\r", "", $2); n=$2 }
            END { print n }')
        case "${size}" in
            ''|*[!0-9]*) echo "RK fix: invalid Content-Length" >&2; return 1 ;;
        esac
        [ "${size}" -gt 0 ] || return 1
        total=$((total + size))
        urls="${urls} ${candidate}"
    done
    [ "${total}" -gt 0 ] || return 1
    available=$(awk '/^MemAvailable:/ { print $2; exit }' /proc/meminfo)
    case "${available}" in
        ''|*[!0-9]*) echo "RK fix: cannot check available RAM" >&2; return 1 ;;
    esac
    # Запас 512 МиБ — минимальная проверка перед скачиванием, не гарантия работы GUI.
    needed=$((total + 536870912))
    if [ $((available * 1024)) -lt "${needed}" ]; then
        echo "RK fix: insufficient available RAM: ${available} KiB; image ${total} bytes + 512 MiB reserve" >&2
        return 1
    fi
    limit=$((total + 16777216))
    echo "RK fix: image ${total} bytes; tmpfs ${limit} bytes; available ${available} KiB"
    mkdir -p "${mountpoint}" || return 1
    mount -t tmpfs -o "size=${limit}" tmpfs "${mountpoint}" || return 1
    mkdir -p "${mountpoint}/casper" || { umount "${mountpoint}"; return 1; }
    image="${mountpoint}/casper/root.squashfs"
    : > "${image}" || { umount "${mountpoint}"; return 1; }
    for candidate in ${urls}; do
        # Не повторяем curl в stdout: повтор может дописать дубликат в общий файл.
        if ! /bin/curl -4 -fL --connect-timeout 15 "${candidate}" >> "${image}"; then
            echo "RK fix: download failed: ${candidate}" >&2
            rm -f "${image}"
            umount "${mountpoint}"
            return 1
        fi
    done
    actual=$(wc -c < "${image}" | tr -d '[:space:]')
    if [ "${actual}" != "${total}" ]; then
        echo "RK fix: incomplete image: expected ${total}, received ${actual}" >&2
        rm -f "${image}"
        umount "${mountpoint}"
        return 1
    fi
    return 0
}
