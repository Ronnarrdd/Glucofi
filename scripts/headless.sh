#!/bin/sh
# Lance une commande GTK dans un compositeur Wayland sans écran (mutter --headless), sur un bus D-Bus à part.
# Pour les captures et les evals de rendu : la fenêtre n'apparaît pas sur le bureau, et aucun compositeur
# ne retient le dessin d'une fenêtre cachée (sur le bureau, une capture sur trois sortait vide).
# Usage : scripts/headless.sh [--size LxH] COMMANDE [ARGS...]
set -eu
SIZE=1280x1200
if [ "${1:-}" = "--size" ]; then
    SIZE=$2
    shift 2
fi
if [ $# -eq 0 ]; then
    echo "usage : $0 [--size LxH] COMMANDE [ARGS...]" >&2
    exit 2
fi
for tool in mutter dbus-run-session; do
    command -v "$tool" >/dev/null || { echo "$tool introuvable : urpmi mutter dbus" >&2; exit 3; }
done
export GLUCOFI_HEADLESS_SIZE="$SIZE"
exec dbus-run-session -- sh -c '
    name="glucofi-headless-$$"
    runtime="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    log="${TMPDIR:-/tmp}/$name.log"
    mutter --headless --wayland --no-x11 --virtual-monitor "$GLUCOFI_HEADLESS_SIZE" --wayland-display "$name" >"$log" 2>&1 &
    compositor=$!
    trap "kill $compositor 2>/dev/null; wait $compositor 2>/dev/null; rm -f \"$log\"" EXIT
    tries=0
    while [ ! -S "$runtime/$name" ]; do
        tries=$((tries + 1))
        if [ $tries -gt 100 ] || ! kill -0 $compositor 2>/dev/null; then
            echo "mutter sans écran n’a pas démarré :" >&2
            cat "$log" >&2
            exit 3
        fi
        sleep 0.1
    done
    status=0
    WAYLAND_DISPLAY="$name" GDK_BACKEND=wayland GDK_DEBUG=no-portals ADW_DISABLE_PORTAL=1 GTK_A11Y=none "$@" || status=$?
    exit $status
' sh "$@"
