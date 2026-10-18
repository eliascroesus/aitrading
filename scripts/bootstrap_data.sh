#!/usr/bin/env bash
# Fetch the offline reference data the system needs when its cache is cold.
#
#   bash scripts/bootstrap_data.sh            # small reference files (~12 MB) + seed VIX and BTC/ETH
#   bash scripts/bootstrap_data.sh --history  # also the Qlib US daily 2000-2020 history (~450 MB zip)
#                                             # and seed the parquet cache from it (operator backtests)
#
# Idempotent: anything already present is left alone. Sources are listed in docs/DATA.md.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${HIVE_DATA_DIR:-$ROOT/data}"
RAW="$DATA/raw"; CACHE="$DATA/cache"
HIVE="${HIVE_BIN:-$ROOT/.venv/bin/hive}"; [ -x "$HIVE" ] || HIVE=hive
mkdir -p "$RAW/coinmetrics" "$CACHE"

fetch() {  # fetch <dest> <url>...   the first URL that answers wins
  local dest=$1; shift
  if [ -s "$dest" ]; then echo "present  $dest"; return 0; fi
  for url in "$@"; do
    if curl -fsSL --retry 3 --retry-delay 5 -o "$dest.part" "$url"; then
      mv "$dest.part" "$dest"; echo "fetched  $dest  <- $url"; return 0
    fi
  done
  rm -f "$dest.part"; echo "FAILED   $dest" >&2; return 1
}

fetch "$RAW/sp500_pit_membership.csv" \
  "https://raw.githubusercontent.com/fja05680/sp500/master/S%26P%20500%20Historical%20Components%20%26%20Changes(08-01-2025).csv" \
  "https://raw.githubusercontent.com/hanshof/sp500_constituents/main/sp_500_historical_components.csv"
fetch "$RAW/sp500_constituents.csv" \
  "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
fetch "$RAW/vix-daily.csv" \
  "https://raw.githubusercontent.com/datasets/finance-vix/main/data/vix-daily.csv"
fetch "$RAW/coinmetrics/btc.csv" "https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv"
fetch "$RAW/coinmetrics/eth.csv" "https://raw.githubusercontent.com/coinmetrics/data/master/csv/eth.csv"

first_bar() { grep -o '"first": "[^"]*' "$1" 2>/dev/null | cut -d'"' -f4; }

if [ "${1:-}" = "--history" ]; then
  spy_first="$(first_bar "$CACHE/etf/SPY.json" || true)"
  if [ -n "$spy_first" ] && [[ "$spy_first" < "2001-01-02" ]]; then
    echo "present  $CACHE (SPY history starts $spy_first)"
  else
    TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
    echo "downloading Qlib US daily (~450 MB) ..."
    curl -fsSL --retry 3 --retry-delay 10 -o "$TMP/qlib.zip" \
      "https://github.com/SunsetWolf/qlib_dataset/releases/download/v0/qlib_data_us_1d_latest.zip"
    unzip -q "$TMP/qlib.zip" -d "$TMP/qlib"
    QLIB_ROOT="$(dirname "$(find "$TMP/qlib" -type d -name calendars | head -1)")"
    "$HIVE" data seed --qlib-root "$QLIB_ROOT"
  fi
elif [ ! -f "$CACHE/index/VIX.json" ]; then
  "$HIVE" data seed          # VIX + BTC/ETH from the files above; stocks come from `hive data refresh`
fi
