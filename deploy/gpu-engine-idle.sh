#!/bin/sh
set -eu
for peer in kmic-qwen27b.service p100-lab-kmic-moe.service p100-lab-strata.service p100-lab-scream.service p100-lab-scream-512k.service p100-lab-scream-two-agent.service p100-lab-inkling.service p100-lab-pxa.service p100-lab-qr0n.service p100-scream-build.service p100-scream-setup.service; do
    [ "$peer" != "$1" ] || continue
    state=$(/usr/bin/systemctl --user show "$peer" --property=ActiveState --value)
    case "$state" in
        inactive|failed) ;;
        *) printf '%s is using or starting on the GPUs\n' "$peer" >&2; exit 1 ;;
    esac
done
sample=$(/usr/bin/nvidia-smi --query-gpu=memory.free,temperature.gpu --format=csv,noheader,nounits)
printf '%s\n' "$sample" | awk -F, 'NF != 2 || $1 !~ /^[[:space:]]*[0-9]+[[:space:]]*$/ ||
    $2 !~ /^[[:space:]]*[0-9]+[[:space:]]*$/ || $1 < 14000 || $2 >= 75 { bad=1 }
    END { if (bad || NR != 2) { print "Both P100s must be idle and below 75 C" > "/dev/stderr"; exit 1 } }'
