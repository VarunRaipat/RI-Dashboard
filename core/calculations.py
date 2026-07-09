from core.config import (
    PRODUCT_CONFIG, RAW_MATERIALS, PIPE_DIAMETER_CONFIG, PRICING_KEY_TO_DIAMETER_MM,
    EMI_PER_ENTRY, POWER_PER_ENTRY, ADMIN_PER_ENTRY, MISC_PCT, GST_PCT,
)


def calculate_production(
    product: str,
    nos: float,
    rm_prices: dict,
    product_config: dict = None,
    raw_materials: list = None,
    pipe_diameter_config: dict = None,
    entry_count: int = 1,
) -> dict:
    """
    Nothing is entered per DPR batch — Concrete and Steel each have a fixed
    per-unit quantity set on the product (see RAW_MATERIALS's product_field),
    so usage = Nos x that figure, and cost = usage x the matching RM Prices
    rate. Jalli (cage welding), Welding, Production, and Loading/Unloading
    are flat Rs./nos rates — not priced materials. Power (which also covers
    DG), like EMI/Admin, is a flat Rs./day cost — EMI_PER_ENTRY/POWER_PER_ENTRY/
    ADMIN_PER_ENTRY are each one day's full share, so when a day has more than
    one product line, entry_count (the number of lines saved for that day)
    splits it evenly across them — otherwise a 5-product day would charge 5x
    a single day's EMI/Power/Admin instead of 1x.

    For Hume Pipes, the per-unit flat rates come from pipe_diameter_config
    (keyed by diameter only, since they don't vary by class or Joint Type),
    while selling_price and concrete_volume_m3 come from product_config
    (keyed by diameter+class, since those DO vary). Non-pipe products keep
    everything in product_config as a single flat dict.
    """
    cfg = dict((product_config or PRODUCT_CONFIG)[product])
    diameter = PRICING_KEY_TO_DIAMETER_MM.get(product)
    if diameter is not None:
        shared = (pipe_diameter_config or PIPE_DIAMETER_CONFIG).get(diameter, {})
        cfg = {**shared, **cfg}  # cfg's own selling_price/concrete_volume_m3 take precedence
    materials = raw_materials or RAW_MATERIALS

    def v(x):
        return float(x or 0)

    usage = {m["key"]: v(cfg.get(m["product_field"], 0)) * v(nos) for m in materials}
    rm_cost = sum(usage[k] * rm_prices.get(k, 0) for k in usage)

    production_cost        = cfg["production_cost"] * v(nos)
    loading_unloading_cost = cfg["loading_unloading_cost"] * v(nos)
    welding_cost             = cfg["welding_cost"] * v(nos)
    jalli_cost               = cfg["jalli_cost"] * v(nos)
    divisor    = max(int(entry_count or 1), 1)
    emi_cost   = float(EMI_PER_ENTRY)   / divisor  # day's EMI, split across today's lines
    power_cost = float(POWER_PER_ENTRY) / divisor  # day's Power (incl. DG), split across today's lines
    admin_cost = float(ADMIN_PER_ENTRY) / divisor  # day's Admin, split across today's lines

    sub_total  = (rm_cost + production_cost + loading_unloading_cost
                  + welding_cost + jalli_cost + emi_cost + power_cost + admin_cost)
    misc_cost  = sub_total * (MISC_PCT / 100)
    total_cost = sub_total + misc_cost

    revenue    = cfg["selling_price"] * v(nos)
    profit     = revenue - total_cost
    profit_pct = (profit / revenue * 100) if revenue > 0 else 0

    costs = {
        "rm_cost":                 round(rm_cost, 2),
        "production_cost":         round(production_cost, 2),
        "loading_unloading_cost":  round(loading_unloading_cost, 2),
        "power_cost":              round(power_cost, 2),
        "welding_cost":            round(welding_cost, 2),
        "jalli_cost":              round(jalli_cost, 2),
        "emi_cost":                round(emi_cost, 2),
        "admin_cost":              round(admin_cost, 2),
        "misc_cost":               round(misc_cost, 2),
        "total_cost":              round(total_cost, 2),
        "revenue":                 round(revenue, 2),
        "profit":                  round(profit, 2),
        "profit_pct":              round(profit_pct, 2),
    }
    for m in materials:
        costs[f"{m['key']}_qty"]  = round(usage[m["key"]], 3)
        costs[f"{m['key']}_cost"] = round(usage[m["key"]] * rm_prices.get(m["key"], 0), 2)
    return costs


def dispatch_value(qty: float, rate: float) -> float:
    return round(float(qty or 0) * float(rate or 0), 2)


def gst_split(base_amount: float, gst_applicable: bool) -> tuple:
    """Given a GST-exclusive base amount (qty x rate), return
    (gst_amount, total_incl_gst) — gst_amount is 0 when not applicable."""
    base = float(base_amount or 0)
    gst_amount = round(base * GST_PCT / 100, 2) if gst_applicable else 0.0
    return gst_amount, round(base + gst_amount, 2)


def transport_charge(mode: str, rate: float, qty: float, gst_applicable: bool) -> tuple:
    """Transport is billed either "per_unit" (rate x qty, same shape as
    Material Rate) or "flat" (rate is the total amount as-is — for a flat
    charge split across multiple product lines in one challan/DI, the
    caller passes the full rate for exactly one line and 0 for the rest,
    so summing across lines never double-counts). Returns
    (transport_value, transport_gst_amount) — GST on transport is tracked
    separately from Material GST since real invoices don't always tax both
    the same way."""
    value = round(float(rate or 0) * float(qty or 0), 2) if mode == "per_unit" else round(float(rate or 0), 2)
    gst_amount = round(value * GST_PCT / 100, 2) if gst_applicable else 0.0
    return value, gst_amount
