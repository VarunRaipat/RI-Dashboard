from core.config import (
    PRODUCT_CONFIG, RAW_MATERIALS, STEEL_MATERIAL, EMI_PER_ENTRY, DG_PER_ENTRY, ADMIN_PER_ENTRY, MISC_PCT,
)


def calculate_production(
    product: str,
    nos: float,
    rm_used: dict,
    rm_prices: dict,
    product_config: dict = None,
    raw_materials: list = None,
) -> dict:
    """
    rm_used: {material_key: qty_in_native_unit} for batch-entered materials
    only — e.g. {"cement_ppc": 40, "ggbs": 10}. Steel is NOT passed in here:
    it's computed as Nos x the product's fixed steel_kg_per_unit and merged
    in below, since steel usage is known per product rather than measured
    per batch.

    Iterates over `raw_materials` (defaults to config.RAW_MATERIALS) so adding
    or renaming a batch-entered material for a different company/product
    line only requires editing that config list, not this function.
    """
    cfg = (product_config or PRODUCT_CONFIG)[product]
    materials = list(raw_materials or RAW_MATERIALS) + [STEEL_MATERIAL]

    def v(x):
        return float(x or 0)

    full_rm_used = dict(rm_used)
    full_rm_used[STEEL_MATERIAL["key"]] = v(cfg.get("steel_kg_per_unit", 0)) * v(nos)

    weights_kg = {m["key"]: v(full_rm_used.get(m["key"], 0)) * m["kg_per_unit"] for m in materials}
    total_wt   = sum(weights_kg.values())

    def pct(w):
        return round(w / total_wt * 100, 2) if total_wt > 0 else 0.0

    rm_pct = {f"pct_{k}": pct(w) for k, w in weights_kg.items()}
    rm_pct["total_wt_kg"] = round(total_wt, 2)

    rm_cost = sum(weights_kg[k] * rm_prices.get(k, 0) for k in weights_kg)

    # No transport term here — real transport cost is tracked separately in
    # the Dispatch module (truck, trip distance, diesel), so a per-unit
    # transport rate at the production-cost level would double-count it.
    labour_cost = (cfg["labour_production"] + cfg["labour_loading"]) * v(nos)
    power_cost  = cfg["power_per_block"] * v(nos)
    emi_cost    = float(EMI_PER_ENTRY)    # fixed Rs./entry
    dg_cost     = float(DG_PER_ENTRY)     # fixed Rs./entry
    admin_cost  = float(ADMIN_PER_ENTRY)  # fixed Rs./entry

    sub_total  = rm_cost + labour_cost + power_cost + emi_cost + dg_cost + admin_cost
    misc_cost  = sub_total * (MISC_PCT / 100)
    total_cost = sub_total + misc_cost

    revenue    = cfg["selling_price"] * v(nos)
    profit     = revenue - total_cost
    profit_pct = (profit / revenue * 100) if revenue > 0 else 0

    costs = {
        "rm_cost":        round(rm_cost, 2),
        "labour_cost":    round(labour_cost, 2),
        "power_cost":     round(power_cost, 2),
        "emi_cost":       round(emi_cost, 2),
        "dg_cost":        round(dg_cost, 2),
        "admin_cost":     round(admin_cost, 2),
        "misc_cost":      round(misc_cost, 2),
        "total_cost":     round(total_cost, 2),
        "revenue":        round(revenue, 2),
        "profit":         round(profit, 2),
        "profit_pct":     round(profit_pct, 2),
    }
    costs.update(rm_pct)
    for m in materials:
        costs[f"{m['key']}_qty"] = round(v(full_rm_used.get(m["key"], 0)), 3)
    return costs


def dispatch_value(qty: float, rate: float) -> float:
    return round(float(qty or 0) * float(rate or 0), 2)
