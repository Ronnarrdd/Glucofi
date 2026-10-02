#!/bin/sh
# Partie système de Glucofi, à lancer avec sudo :
# - dépendances Mageia (GTK4, libadwaita, matplotlib, reportlab, compilateur, libusb) ;
# - compilation d'accuchek depuis services/device/accuchek-src vers /usr/local/bin/accuchek ;
# - règle udev donnant l'accès USB au lecteur à l'utilisateur de la session
#   locale active : accuchek tourne sans root.
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "À lancer avec sudo : sudo $0" >&2
    exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)"
ACCUCHEK_SRC="$SRC/../services/device/accuchek-src"

urpmi --auto python3-gobject lib64gtk4_1 lib64gtk-gir4.0 lib64adwaita1_0 lib64adwaita-gir1 \
    python3-matplotlib python3-reportlab gcc-c++ make lib64usb1.0-devel

# compilé hors du dépôt : des fichiers root dans accuchek-src empêcheraient
# ensuite scripts/gate.sh (et le hook pre-commit) de recompiler sans sudo
BUILD="$(mktemp -d /tmp/glucofi-accuchek-build.XXXXXX)"
trap 'rm -rf "$BUILD"' EXIT
cp -r "$ACCUCHEK_SRC/." "$BUILD/"
rm -rf "$BUILD/.objs" "$BUILD/.deps" "$BUILD/accuchek"
make -C "$BUILD" all
install -o root -g root -m 755 "$BUILD/accuchek" /usr/local/bin/accuchek
# anciennes installations : sorties de compilation root dans le dépôt
rm -rf "$ACCUCHEK_SRC/.objs" "$ACCUCHEK_SRC/.deps"
rm -f "$ACCUCHEK_SRC/accuchek"

install -o root -g root -m 644 "$SRC/udev/70-glucofi-accuchek.rules" /etc/udev/rules.d/70-glucofi-accuchek.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match=idVendor=173a

# anciennes installations : lanceur root, polkit et config.txt ne servent plus
rm -f /usr/local/libexec/glucofi-accuchek \
    /usr/share/polkit-1/actions/fr.librenard.glucofi.accuchek.policy \
    /usr/local/share/glucofi/config.txt
rmdir /usr/local/share/glucofi 2>/dev/null || true

echo "Partie système installée. Test sans sudo, lecteur branché : accuchek > /tmp/mesures.json"
