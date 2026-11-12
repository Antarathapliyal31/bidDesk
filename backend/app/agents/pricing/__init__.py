"""Pricing Agent — queries the catalog via the semantic layer, proposes
SKU matches / substitutes / assumptions. Owns the only path to catalog
data (SPEC §4.1). Prices come from the DB row, never from the model.
"""
