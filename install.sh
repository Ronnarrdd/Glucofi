#!/bin/sh
# Installe Glucofi pour l'utilisateur courant (sans sudo) dans ~/.local.
# La partie système (accuchek, règle udev, matplotlib) est dans packaging/install-system.sh, à lancer avec sudo.
set -eu

SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
LIB="$PREFIX/lib/glucofi"
BIN="$PREFIX/bin"
APPS="$PREFIX/share/applications"
ICONS="$PREFIX/share/icons/hicolor/scalable/apps"

mkdir -p "$LIB" "$BIN" "$APPS" "$ICONS"
rm -rf "$LIB/app" "$LIB/contracts" "$LIB/services"
for dir in app contracts services; do
    cp -r "$SRC/$dir" "$LIB/$dir"
done
rm -rf "$LIB/services/device/accuchek-src"
find "$LIB" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$LIB" -type d -name tests -prune -exec rm -rf {} +

cat > "$BIN/glucofi" <<EOF
#!/bin/sh
export GLUCOFI_SRC="$SRC"
export PYTHONPATH="$LIB\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m app "\$@"
EOF
chmod 755 "$BIN/glucofi"

install -m 644 "$SRC/packaging/fr.librenard.Glucofi.desktop" "$APPS/fr.librenard.Glucofi.desktop"
install -m 644 "$SRC/packaging/icons/fr.librenard.Glucofi.svg" "$ICONS/fr.librenard.Glucofi.svg"
update-desktop-database "$APPS" 2>/dev/null || true
gtk-update-icon-cache -q "$PREFIX/share/icons/hicolor" 2>/dev/null || true

echo "Glucofi installé dans $LIB (lanceur : $BIN/glucofi)."
if [ ! -f /etc/udev/rules.d/70-glucofi-accuchek.rules ] || ! python3 -c 'import matplotlib' 2>/dev/null; then
    echo "Reste à lancer une fois, en administrateur :"
    echo "  sudo $SRC/packaging/install-system.sh"
fi
