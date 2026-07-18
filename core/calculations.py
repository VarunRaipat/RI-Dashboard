import pandas as pd
from core.config import (
    PRODUCT_CONFIG, RAW_MATERIALS, PIPE_DIAMETER_CONFIG, PRICING_KEY_TO_DIAMETER_MM,
    SKU_TO_PRICING_KEY, EMI_PER_DAY, POWER_PER_DAY, ADMIN_PER_DAY, MISC_PCT, GST_PCT,
    REPAIRING_PCT_OF_PRODUCTION,
)


def calculate_production(
    product: str,
    nos: float,
    rm_prices: dict,
    product_config: dict = None,
    raw_materials: list = None,
    pipe_diameter_config: dict = None,
) -> dict:
    """
    Nothing is entered per DPR batch — Concrete and Steel each have a fixed
    per-unit quantity set on the product (see RAW_MATERIALS's product_field),
    so usage = Nos x that figure, and cost = usage x the matching RM Prices
    rate. Jalli (cage welding), Welding, Production, and Loading/Unloading
    are flat Rs./nos rates — not priced materials.

    EMI/Power/Admin are NOT included here — they're whole-factory overheads
    that don't scale with which product (or how many nos) was made, so
    attributing a slice of them to one DPR line was misleading (e.g. making
    1 pipe and 10 pipes on the same day previously got charged the same EMI
    share each). They're charged once per production day instead, at the
    period level — see daily_fixed_costs() below, used by the Dashboard.

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

    sub_total  = rm_cost + production_cost + loading_unloading_cost + welding_cost + jalli_cost
    misc_cost  = rm_cost * (MISC_PCT / 100)
    total_cost = sub_total + misc_cost

    revenue    = cfg["selling_price"] * v(nos)
    profit     = revenue - total_cost
    profit_pct = (profit / revenue * 100) if revenue > 0 else 0

    costs = {
        "rm_cost":                 round(rm_cost, 2),
        "production_cost":         round(production_cost, 2),
        "loading_unloading_cost":  round(loading_unloading_cost, 2),
        "power_cost":              0.0,  # factory-level, see daily_fixed_costs()
        "welding_cost":            round(welding_cost, 2),
        "jalli_cost":              round(jalli_cost, 2),
        "emi_cost":                0.0,  # factory-level, see daily_fixed_costs()
        "admin_cost":              0.0,  # factory-level, see daily_fixed_costs()
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


def daily_fixed_costs(production_days: int) -> dict:
    """EMI + Power (incl. DG) + Admin, charged once per calendar day that had
    at least one production entry — never per product/entry. These are
    whole-factory overheads that run the same whether one line or ten was
    logged that day, so splitting them across DPR lines (the old approach)
    made a small day look disproportionately unprofitable and a big day
    look disproportionately profitable. Callers (Dashboard) pass the count
    of distinct dates-with-production in the period being viewed."""
    days = max(int(production_days or 0), 0)
    emi_total   = round(EMI_PER_DAY * days, 2)
    power_total = round(POWER_PER_DAY * days, 2)
    admin_total = round(ADMIN_PER_DAY * days, 2)
    return {
        "emi_cost":   emi_total,
        "power_cost": power_total,
        "admin_cost": admin_total,
        "total":      round(emi_total + power_total + admin_total, 2),
    }


def loading_unloading_for_dispatch(df_dispatch: pd.DataFrame, product_config: dict = None,
                                    pipe_diameter_config: dict = None) -> float:
    """Total Loading/Unloading cost for the given (already date-filtered)
    dispatch rows: qty_dispatched x the product's Loading/Unloading rate
    (Pipe Diameter Rates for Hume Pipes, Product Cost Configuration for
    everything else — same rate table DPR uses, see calculate_production).
    Costed off Dispatch quantity rather than DPR Nos, since loading/
    unloading labour happens when goods go out, not when they're cast."""
    if df_dispatch is None or df_dispatch.empty or "product" not in df_dispatch.columns:
        return 0.0
    cfg_map  = product_config or PRODUCT_CONFIG
    pipe_cfg = pipe_diameter_config or PIPE_DIAMETER_CONFIG

    total = 0.0
    for product, qty in df_dispatch.groupby("product")["qty_dispatched"].sum().items():
        pricing_key = SKU_TO_PRICING_KEY.get(product, product)
        cfg = cfg_map.get(pricing_key)
        if cfg is None:
            continue
        diameter = PRICING_KEY_TO_DIAMETER_MM.get(pricing_key)
        rate = pipe_cfg.get(diameter, {}).get("loading_unloading_cost", 0) if diameter is not None \
            else cfg.get("loading_unloading_cost", 0)
        total += float(rate or 0) * float(qty or 0)
    return round(total, 2)


def liability_totals(df_production: pd.DataFrame, df_dispatch: pd.DataFrame = None,
                      product_config: dict = None, pipe_diameter_config: dict = None) -> dict:
    """Labour cost totals for the given (already date-filtered) period:
    Production + Jalli + Welding (from DPR), Repairing (always
    REPAIRING_PCT_OF_PRODUCTION% of Production cost — not a separate DPR
    figure), and Loading/Unloading (from Dispatch quantity, not DPR Nos —
    see loading_unloading_for_dispatch). "total_cost" is the plain sum of
    all five — no percentage/markup applied."""
    if df_production is not None and not df_production.empty:
        production_cost = float(df_production.get("production_cost", 0).sum())
        welding_cost    = float(df_production.get("welding_cost", 0).sum())
        jalli_cost      = float(df_production.get("jalli_cost", 0).sum())
    else:
        production_cost = welding_cost = jalli_cost = 0.0

    repairing_cost         = production_cost * (REPAIRING_PCT_OF_PRODUCTION / 100)
    loading_unloading_cost = loading_unloading_for_dispatch(df_dispatch, product_config, pipe_diameter_config)
    total_cost = production_cost + welding_cost + jalli_cost + repairing_cost + loading_unloading_cost

    return {
        "production_cost":        round(production_cost, 2),
        "welding_cost":           round(welding_cost, 2),
        "jalli_cost":             round(jalli_cost, 2),
        "repairing_cost":         round(repairing_cost, 2),
        "loading_unloading_cost": round(loading_unloading_cost, 2),
        "total_cost":             round(total_cost, 2),
    }


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
