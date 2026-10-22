"""kitelog - offline toolkit for the KiteLog Connect IQ app.

Modules
-------
packing   : bit-packing of 25 Hz accelerometer samples into FIT DOUBLE fields
            (exact mirror of the Monkey C code in watch/source/Pack.mc)
physics   : constants, pressure->height, kite-jump trajectory model
simulate  : synthetic kite session generator (ground truth + sensor records)
fitio     : FIT decode (garmin-fit-sdk) and synthetic FIT encode (fit_tool)
detect    : jump detection from accel magnitude + barometer + GPS
height    : jump-height estimators (barometer fit, airtime model)
session   : session level statistics (tacks, speed, distance)
report    : plots, CSV/JSON/GPX outputs
"""
__version__ = "0.1.0"
