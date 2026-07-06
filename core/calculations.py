from core.config import (
    PRODUCT_CONFIG, RAW_MATERIALS, EMI_PER_ENTRY, DG_PER_ENTRY, ADMIN_PER_ENTRY, MISC_PCT,
)


def calculate_production(
    product: str,
    nos: float,
    rm_prices: dict,
    product_config: dict = None,
    raw_materials: list = None,
) -> dict:
    """
    Nothing is entered per DPR batch — Concrete and Steel each have a fixed
    per-unit quantity set on the product (see RAW_MATERIALS's product_field),
    so usage = Nos x that figure, and cost = usage x the matching RM Prices
    rate. Jalli (cage welding), Welding, Production, Loading/Unloading, and
    Power are all flat Rs./nos rates on the product — not priced materials.
    """
    cfg = (product_config or PRODUCT_CONFIG)[product]
    materials = raw_materials or RAW_MATERIALS

    def v(x):
        return float(x or 0)

    usage = {m["key"]: v(cfg.get(m["product_field"], 0)) * v(nos) for m in materials}
    rm_cost = sum(usage[k] * rm_prices.get(k, 0) for k in usage)

    production_cost        = cfg["production_cost"] * v(nos)
    loading_unloading_cost = cfg["loading_unloading_cost"] * v(nos)
    power_cost              = cfg["power_per_block"] * v(nos)
    welding_cost             = cfg["welding_cost"] * v(nos)
    jalli_cost               = cfg["jalli_cost"] * v(nos)
    emi_cost   = float(EMI_PER_ENTRY)    # fixed Rs./entry
    dg_cost    = float(DG_PER_ENTRY)     # fixed Rs./entry
    admin_cost = float(ADMIN_PER_ENTRY)  # fixed Rs./entry

    sub_total  = (rm_cost + production_cost + loading_unloading_cost + power_cost
                  + welding_cost + jalli_cost + emi_cost + dg_cost + admin_cost)
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
        "dg_cost":                 round(dg_cost, 2),
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
