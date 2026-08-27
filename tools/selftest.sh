#!/usr/bin/env bash
# End-to-end test of rastertoescpos WITHOUT CUPS or a printer:
#   sample bitmap -> CUPS raster v3 -> rastertoescpos -> disassembly + assertions
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PY:-python3}"
FILTER=src/filter/rastertoescpos
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> generate sample bitmap (512 x 700, bars + gaps)"
$PY - "$WORK/sample.pbm" <<'EOF'
import sys
w, h = 512, 700
row = w // 8
buf = bytearray(row * h)
def hbar(y0, y1):
    for y in range(y0, y1):
        for b in range(row):
            buf[y*row + b] = 0xFF
def box(y0, y1, x0, x1):
    for y in range(y0, y1):
        for x in range(x0, x1):
            buf[y*row + (x >> 3)] |= (0x80 >> (x & 7))
hbar(20, 60)                 # top solid bar
box(120, 200, 40, 300)       # a block
box(260, 320, 200, 470)      # another, offset
hbar(360, 380)
# rows 400..680 left blank on purpose (blank-reduction test)
hbar(690, 700)
open(sys.argv[1], "wb").write(b"P4\n%d %d\n" % (w, h) + bytes(buf))
print("wrote", sys.argv[1])
EOF

run() {   # <label> <options...>
    local label="$1"; shift
    $PY tools/mkraster.py "$WORK/sample.pbm" > "$WORK/page.ras"
    PPD= $PY "$FILTER" 1 tester "selftest:$label" 1 "$*" \
        < "$WORK/page.ras" > "$WORK/$label.escpos" 2> "$WORK/$label.log"
    printf '\n==> %-22s -> %6d bytes\n' "$label" "$(wc -c < "$WORK/$label.escpos")"
    $PY tools/escpos_dis.py "$WORK/$label.escpos" | sed -n '/SUMMARY/,$p'
}

assert_has() {  # <file> <hex-needle> <msg>
    if ! xxd -p "$WORK/$1.escpos" | tr -d '\n' | grep -qi "$2"; then
        echo "FAIL[$1]: expected byte sequence $2 ($3)"; exit 1
    fi
}

run baseline        "Density=normal BlankReduction=off EndOfJobCut=partial"
run dark-full       "Density=dark   BlankReduction=off EndOfJobCut=full"
run reduce-all      "Density=normal BlankReduction=all EndOfJobCut=partial"
run rot-margin      "Rotate180=True LeftMargin=5mm    EndOfJobCut=none"
run two-copies      "Density=light  EndOfJobCut=full"  # copies come from argv[4]

# --- assertions ---------------------------------------------------------------
assert_has baseline  "1d284c"        "GS ( L graphics block"
assert_has baseline  "1d284c02003032" "GS ( L fn=50 print-buffer"
assert_has baseline  "1d5642"         "GS V 66 (partial cut)"
assert_has dark-full "1d5641"         "GS V 65 (full cut)"
assert_has dark-full "1d284b02003106" "GS ( K density=dark (n=6)"
assert_has rot-margin "1d4c2300"      "GS L (left margin = 35 dots)"
if xxd -p "$WORK/rot-margin.escpos" | tr -d '\n' | grep -qi "1d56"; then
    echo "FAIL[rot-margin]: cut emitted despite EndOfJobCut=none"; exit 1
fi

# blank reduction must shrink the stream
b=$(wc -c < "$WORK/baseline.escpos"); r=$(wc -c < "$WORK/reduce-all.escpos")
echo
echo "baseline=$b bytes   reduce-all=$r bytes"
[ "$r" -lt "$b" ] || { echo "FAIL: BlankReduction=all did not shrink output"; exit 1; }

echo
echo "ALL SELFTESTS PASSED"
