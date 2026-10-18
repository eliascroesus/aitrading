"""The operator: the AI's bounded loop for iterating on its own strategy.

It can only (1) propose config variants inside pre-registered bounds,
(2) have code backtest them in- and out-of-sample, (3) register winners as
paper-traded shadow children when its survival tier allows, and (4) recommend
promotion. It cannot change limits, touch the risk engine, spend outside its
budget, or promote anything to live — that last step is a human's.
"""
