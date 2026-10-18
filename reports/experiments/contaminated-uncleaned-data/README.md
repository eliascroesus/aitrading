# First-pass experiment outputs on *uncleaned* data — kept for transparency, do not cite

These CSVs were produced before the data-quality guard (`hive.data.quality`)
existed. The Qlib/Yahoo history contains corrupted prints around delistings
and acquisitions (e.g. `CBE` printed a x1000 close on 2012-11-28, turning one
trade into +105,794%). Eighty-five of ~800 series carried at least one
implausible one-day move. Every number in this folder is therefore suspect;
the cleaned re-runs live in `reports/experiments/clean/` and are the ones
`docs/BACKTEST_RESULTS.md` reports.

The discovery itself is part of the study's record: an unbelievable in-sample
result (29% CAGR with a profit factor of 15) was the tell, and the rule we now
apply — truncate any series at its first > 50% daily move or OHLC inconsistency
— is deliberately conservative.
