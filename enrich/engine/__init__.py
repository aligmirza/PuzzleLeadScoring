"""The waterfall engine: every data point is answered by an ordered set of layers, cheapest first.

    layers.py   the five layers (what each is, what it costs, which data points it can answer)
    merge.py    the one set of quality rules for combining answers from different layers, and the ICP verdict
    resolve.py  runs a company through the layers in order: rules, directories, then the AI and provider answers
                already bought. Which paid work is still needed comes out with the result.

Each data point's own settings (config/datapoints/) decide which layers run for it and when.
"""
