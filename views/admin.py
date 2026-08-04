import re
import json
import streamlit as st
import pandas as pd
from core.config import (
    DEFAULT_RM_PRICES, RM_LABELS, PRODUCT_CONFIG, RAW_MATERIALS, HUME_PIPE_DIAMETERS_MM, GST_PCT,
    PRODUCTION_PRODUCTS, DISPATCH_PRODUCTS, SKU_TO_PRICING_KEY, PLANTS, SALE_TYPES, plant_for_product,
    EMI_PER_DAY, POWER_PER_DAY, ADMIN_PER_DAY, MISC_PCT, selling_price_unit,
    INVENTORY_PRODUCTS, RM_INVENTORY_OPENING, INVENTORY_MATERIAL_LABELS, INVENTORY_ANCHOR_DATE,
)
from core.db import (
    get_rm_prices, save_rm_prices, get_production, get_dispatch, delete_row,
    get_product_config, save_product_config, get_pipe_diameter_config, save_pipe_diameter_config,
    get_orders, update_order, update_dispatch,
    get_activity_log, insert_production, insert_dispatch,
    get_edit_requests, approve_edit_request, reject_edit_request,
    get_inventory_opening, save_inventory_opening, delete_inventory_opening,
    get_custom_products, add_custom_product,
    get_custom_diameters, add_custom_diameter,
)
from core.calculations import calculate_production, dispatch_value, gst_split
from core.ui import sanitize_for_export
from core.ui import interactive_table, date_range_filter, add_ist_timestamp, timestamp_col_config
from core.permissions import has_permission, get_all_users, MODULES, MODULE_LABELS, ROLE_DEFAULTS
from core.db import get_user_permissions, save_user_permission, delete_user_permission

LAKH = 100_000


def show(PLOT):
    role = st.session_state.get("role", "viewer")
    username = st.session_state.get("username")
    can_edit = has_permission(username, role, "admin", "edit")

    st.markdown("""
    <div class="page-title">⚙️ Admin Panel</div>
    <div class="page-subtitle">RM prices · Product config · Data management</div>
    """, unsafe_allow_html=True)
    if not can_edit:
        st.caption("👁️ View-only — you can see configuration and history here, but only Admin can make changes.")

    _pending_reqs = get_edit_requests(status="pending")
    if can_edit and not _pending_reqs.empty:
        st.warning(f"📝 **{len(_pending_reqs)} edit request(s)** waiting for review — see the "
                   f"**Edit Requests** tab below.")

    tab1, tab2, tab2b, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs(
        ["💰 RM Prices", "📦 Product Config", "🏭 Inventory Opening", "📋 All Production", "🚚 All Dispatch",
         "🧩 Merge Client Names", "🕵️ Activity Log", f"📝 Edit Requests ({len(_pending_reqs)})",
         "👤 User Permissions"]
    )

    # ── Tab 1: RM Prices ──────────────────────────────────────────────────────
    with tab1:
        st.markdown("### Current Raw Material Prices")
        st.caption("These prices are used in all DPR cost calculations.")

        current = get_rm_prices()

        if can_edit:
            with st.form("rm_prices_form"):
                cols = st.columns(2)
                new_prices = {}
                for i, (key, label) in enumerate(RM_LABELS.items()):
                    col = cols[i % 2]
                    new_prices[key] = col.number_input(
                        label,
                        value=float(current.get(key, DEFAULT_RM_PRICES.get(key, 0))),
                        min_value=0.0, step=0.01,
                    )
                if st.form_submit_button("💾 Save Prices", type="primary", use_container_width=True):
                    save_rm_prices(new_prices)
                    st.success("✅ RM prices updated! New DPR entries will use these prices.")
                    st.rerun()
        else:
            rm_rows = [{"Material": label, "Price (₹)": current.get(key, DEFAULT_RM_PRICES.get(key, 0))}
                       for key, label in RM_LABELS.items()]
            st.dataframe(pd.DataFrame(rm_rows), use_container_width=True, hide_index=True)

    # ── Tab 2: Product Config ─────────────────────────────────────────────────
    with tab2:
        st.markdown("### Product Cost Configuration")

        cfg_sub1, cfg_sub2, cfg_sub3, cfg_sub4 = st.tabs(
            ["💲 Selling Price & Concrete", "📏 Pipe Diameter Rates", "➕ Add New Product", "➕ Add New Diameter"]
        )

        # For Hume Pipes, Production/Loading/Power/Welding/Jalli/Steel are the
        # same for a given diameter regardless of class or Joint Type — set
        # once per diameter in the second sub-tab. Only Selling Price and
        # Concrete Volume vary by class, so they're edited per product here.
        with cfg_sub1:
            st.caption("Selling Price and Concrete Volume (m³) per product. Concrete Volume is "
                       "pre-computed from diameter+barrel thickness for Hume Pipes. For pipes, "
                       "Production/Loading/Power/Welding/Jalli/Steel are set once per diameter in "
                       "the **Pipe Diameter Rates** tab — they don't vary by class or Joint Type.")

            cfg_all = get_product_config()

            if can_edit:
                products = list(cfg_all.keys())
                sel_prod = st.selectbox("Select Product to Edit", products, key="cfg_prod_sel")
                cfg = cfg_all[sel_prod]
                is_pipe = sel_prod.startswith("Hume Pipe")

                with st.form("product_cfg_form"):
                    _unit = selling_price_unit(sel_prod)
                    new_sell = st.number_input(f"Selling Price (Rs./{_unit})", value=float(cfg["selling_price"]), min_value=0.0, step=0.5)
                    st.caption(f"Invoice total incl. {GST_PCT:.0f}% GST: ₹{cfg['selling_price'] * (1 + GST_PCT/100):,.2f}/{_unit} "
                               f"— GST is collected from the customer but owed to the government, so it's shown here for "
                               f"reference only and never counted as profit.")

                    payload = {"selling_price": new_sell}

                    if is_pipe:
                        new_concrete = st.number_input(
                            "Concrete (m³/Unit)", value=float(cfg.get("concrete_volume_m3", 0)),
                            min_value=0.0, step=0.001, format="%.4f",
                        )
                        payload["concrete_volume_m3"] = new_concrete
                    else:
                        cc1, cc2 = st.columns(2)
                        new_prod = cc1.number_input("Production Cost (Rs./nos)",       value=float(cfg.get("production_cost", 0)), min_value=0.0, step=0.05)
                        new_lu   = cc2.number_input("Loading/Unloading Cost (Rs./nos)", value=float(cfg.get("loading_unloading_cost", 0)), min_value=0.0, step=0.05)

                        cc3, cc4 = st.columns(2)
                        new_weld = cc3.number_input("Welding Cost (Rs./nos)",             value=float(cfg.get("welding_cost", 0)), min_value=0.0, step=0.05)
                        new_jalli = cc4.number_input("Jalli — Cage Welding (Rs./nos)",    value=float(cfg.get("jalli_cost", 0)), min_value=0.0, step=0.05)

                        cc5, cc6 = st.columns(2)
                        new_concrete = cc5.number_input("Concrete (m³/Unit)",         value=float(cfg.get("concrete_volume_m3", 0)), min_value=0.0, step=0.001, format="%.4f")
                        new_steel    = cc6.number_input("Steel (Kg/Unit)", value=float(cfg.get("steel_kg_per_unit", 0)), min_value=0.0, step=0.1)

                        payload.update({
                            "production_cost":        new_prod,
                            "loading_unloading_cost": new_lu,
                            "welding_cost":            new_weld,
                            "jalli_cost":              new_jalli,
                            "concrete_volume_m3":      new_concrete,
                            "steel_kg_per_unit":       new_steel,
                        })

                    st.caption(
                        f"Factory-wide fixed costs, charged once per production day (not per product): "
                        f"EMI ₹{EMI_PER_DAY:,.2f} · Power (incl. DG) ₹{POWER_PER_DAY:,.0f} · "
                        f"Admin ₹{ADMIN_PER_DAY:,.0f} · Misc {MISC_PCT:.0f}% (on this product's raw material cost)"
                    )

                    if st.form_submit_button("💾 Save", type="primary", use_container_width=True):
                        save_product_config(sel_prod, payload)
                        st.success(f"✅ {sel_prod} config saved.")
                        st.rerun()

            st.markdown("---")
            st.markdown(f"**Current config — all products** (Sell incl. GST = Sell x {1 + GST_PCT/100:.2f}, reference only)")
            rows = []
            for prod, c in cfg_all.items():
                row = {
                    "Product": prod, "Sell (₹)": c["selling_price"],
                    "Sell incl. GST (₹)": round(c["selling_price"] * (1 + GST_PCT / 100), 2),
                    "Concrete (m³)": c.get("concrete_volume_m3", 0),
                }
                if not prod.startswith("Hume Pipe"):
                    row.update({
                        "Production":     c.get("production_cost", 0),
                        "Loading/Unload": c.get("loading_unloading_cost", 0),
                        "Welding":        c.get("welding_cost", 0),
                        "Jalli":          c.get("jalli_cost", 0),
                        "Steel (Kg)":     c.get("steel_kg_per_unit", 0),
                    })
                rows.append(row)
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        with cfg_sub2:
            st.caption("These 5 rates apply to every class (NP2/NP3/NP4) and Joint Type at the "
                       "selected diameter — set once per diameter, not per SKU. Power is a factory-wide "
                       "fixed cost charged once per production day (Dashboard), not set here.")

            dia_cfg_all = get_pipe_diameter_config()

            if can_edit:
                sel_dia = st.selectbox("Select Diameter (mm)", HUME_PIPE_DIAMETERS_MM, key="dia_cfg_sel")
                dcfg = dia_cfg_all[sel_dia]

                with st.form("pipe_dia_cfg_form"):
                    dc1, dc2 = st.columns(2)
                    d_prod = dc1.number_input("Production Cost (Rs./nos)",       value=float(dcfg.get("production_cost", 0)), min_value=0.0, step=0.05)
                    d_lu   = dc2.number_input("Loading/Unloading Cost (Rs./nos)", value=float(dcfg.get("loading_unloading_cost", 0)), min_value=0.0, step=0.05)

                    dc3, dc4 = st.columns(2)
                    d_weld  = dc3.number_input("Welding Cost (Rs./nos)",           value=float(dcfg.get("welding_cost", 0)), min_value=0.0, step=0.05)
                    d_jalli = dc4.number_input("Jalli — Cage Welding (Rs./nos)",   value=float(dcfg.get("jalli_cost", 0)), min_value=0.0, step=0.05)

                    d_steel = st.number_input("Steel (Kg/Unit)", value=float(dcfg.get("steel_kg_per_unit", 0)), min_value=0.0, step=0.1)

                    if st.form_submit_button("💾 Save", type="primary", use_container_width=True):
                        save_pipe_diameter_config(sel_dia, {
                            "production_cost":        d_prod,
                            "loading_unloading_cost": d_lu,
                            "welding_cost":           d_weld,
                            "jalli_cost":             d_jalli,
                            "steel_kg_per_unit":      d_steel,
                        })
                        st.success(f"✅ {sel_dia}mm diameter rates saved.")
                        st.rerun()

            st.markdown("---")
            st.markdown("**Current rates — all diameters**")
            drows = []
            for d, c in dia_cfg_all.items():
                drows.append({
                    "Diameter (mm)":  d,
                    "Production":     c.get("production_cost", 0),
                    "Loading/Unload": c.get("loading_unloading_cost", 0),
                    "Welding":        c.get("welding_cost", 0),
                    "Jalli":          c.get("jalli_cost", 0),
                    "Steel (Kg)":     c.get("steel_kg_per_unit", 0),
                })
            st.dataframe(pd.DataFrame(drows), use_container_width=True, hide_index=True)

        # ── Add New Product ──────────────────────────────────────────────────
        with cfg_sub3:
            st.caption(
                "Add a brand new non-pipe product (e.g. a new precast item). It shows up "
                "immediately in DPR Entry, Sales Order, Dispatch, Inventory, and above in "
                "**Selling Price & Concrete** — set its rates there right after adding it. "
                "Hume Pipe diameters/classes aren't addable here since they need engineering "
                "data (barrel thickness, joint types) wired in by code."
            )

            existing_custom = get_custom_products()

            if can_edit:
                with st.form("add_product_form", clear_on_submit=True):
                    new_name = st.text_input("Product Name", placeholder="e.g. Manhole Cover")
                    new_unit = st.selectbox("Selling Price Unit", ["nos", "sqft"], index=0)
                    if st.form_submit_button("➕ Add Product", type="primary", use_container_width=True):
                        cleaned = new_name.strip()
                        if not cleaned:
                            st.error("Enter a product name.")
                        elif cleaned in PRODUCT_CONFIG:
                            st.error(f"\"{cleaned}\" already exists.")
                        elif cleaned.startswith("Hume Pipe"):
                            st.error("Hume Pipe products can't be added here — ask to have a new "
                                      "diameter/class wired in with its engineering data.")
                        else:
                            add_custom_product(cleaned, new_unit, st.session_state.get("username") or "")
                            st.success(f"✅ \"{cleaned}\" added — set its rates in "
                                       f"**Selling Price & Concrete** above.")
                            st.rerun()

            st.markdown("---")
            st.markdown("**Admin-added products**")
            if existing_custom:
                st.dataframe(
                    pd.DataFrame([{"Product": r["name"], "Unit": r.get("unit", "nos"),
                                    "Added By": r.get("added_by", ""), "Added": r.get("created_at", "")}
                                  for r in existing_custom]),
                    use_container_width=True, hide_index=True,
                )
            else:
                st.caption("None yet — every product currently in the app was set up in code.")

        # ── Add New Diameter ─────────────────────────────────────────────────
        with cfg_sub4:
            st.caption(
                "Add a brand new Hume Pipe diameter. NP2/NP3/NP4 (Collar, Socket & Spigot, "
                "M/F) and their sharing rules apply automatically, same as every built-in "
                "diameter — the only thing needed is barrel thickness (mm) per class, since "
                "that's real engineering data, not a rate. Leave a class's thickness at 0 if "
                "it isn't actually made at this diameter (matches how a few existing diameters "
                "already have a 0-thickness gap for NP2). After adding, set Production/Loading/"
                "Welding/Jalli/Steel rates in **Pipe Diameter Rates**, and Selling Price in "
                "**Selling Price & Concrete**, for each class."
            )

            existing_diameters = get_custom_diameters()

            if can_edit:
                with st.form("add_diameter_form", clear_on_submit=True):
                    new_dia = st.number_input("Diameter (mm)", min_value=1, step=10)
                    dc1, dc2 = st.columns(2)
                    new_np2_t = dc1.number_input("NP2 Barrel Thickness (mm)", min_value=0.0, step=1.0,
                                                  help="Leave at 0 if NP2 isn't made at this diameter.")
                    new_np3_t = dc2.number_input("NP3 Barrel Thickness (mm)", min_value=0.0, step=1.0,
                                                  help="NP4 uses this same thickness (it's the same physical pipe as NP3).")
                    if st.form_submit_button("➕ Add Diameter", type="primary", use_container_width=True):
                        if int(new_dia) in HUME_PIPE_DIAMETERS_MM:
                            st.error(f"{int(new_dia)}mm already exists.")
                        elif not new_np2_t and not new_np3_t:
                            st.error("Enter at least one class's barrel thickness.")
                        else:
                            add_custom_diameter(int(new_dia), new_np2_t, new_np3_t,
                                                 st.session_state.get("username") or "")
                            st.success(f"✅ {int(new_dia)}mm added — set its rates in "
                                       f"**Pipe Diameter Rates** and selling price in "
                                       f"**Selling Price & Concrete**.")
                            st.rerun()

            st.markdown("---")
            st.markdown("**Admin-added diameters**")
            if existing_diameters:
                st.dataframe(
                    pd.DataFrame([{"Diameter (mm)": r["diameter_mm"],
                                    "NP2 Thickness (mm)": r.get("np2_thickness_mm", 0),
                                    "NP3 Thickness (mm)": r.get("np3_thickness_mm", 0),
                                    "Added By": r.get("added_by", ""), "Added": r.get("created_at", "")}
                                  for r in existing_diameters]),
                    use_container_width=True, hide_index=True,
                )
            else:
                st.caption("None yet — every diameter currently in the app was set up in code.")

    # ── Tab 2b: Inventory Opening Balances ────────────────────────────────────
    with tab2b:
        st.markdown("### Inventory Opening Balances")
        st.caption(
            f"Opening stock as counted on {pd.Timestamp(INVENTORY_ANCHOR_DATE).strftime('%d-%b-%Y')}. "
            "Current stock everywhere else in the app = this opening qty + received/produced − "
            "consumed/dispatched since that date. Set these here directly — no code changes needed."
        )

        db_opening = get_inventory_opening()

        fg_sub, rm_sub = st.tabs(["📦 Finished Goods", "🧱 Raw Materials"])

        with fg_sub:
            fg_options = [row[0] for row in INVENTORY_PRODUCTS]
            fg_defaults = {row[0]: row[3] for row in INVENTORY_PRODUCTS}

            if can_edit:
                sel_fg = st.selectbox("Select Product", fg_options, key="inv_open_fg_sel")
                override = db_opening.get(sel_fg)
                current_val = override["qty"] if override else fg_defaults[sel_fg]

                with st.form("inv_open_fg_form"):
                    new_val = st.number_input(
                        "Opening Qty (Nos)", value=float(current_val), min_value=0.0, step=1.0,
                    )
                    if override:
                        st.caption(f"Currently a manual override — last set to {override['qty']:.0f} "
                                   f"by {override['updated_by'] or 'unknown'} on {override['updated_at']}.")
                    else:
                        st.caption(f"Currently using the hardcoded default ({fg_defaults[sel_fg]:.0f}).")
                    fc1, fc2 = st.columns(2)
                    save_fg = fc1.form_submit_button("💾 Save Opening Qty", type="primary", use_container_width=True)
                    reset_fg = fc2.form_submit_button("↩️ Reset to Default", use_container_width=True,
                                                       disabled=not override)
                    if save_fg:
                        save_inventory_opening(sel_fg, "finished_good", new_val,
                                                st.session_state.get("username") or "")
                        st.success(f"✅ Opening qty for {sel_fg} set to {new_val:.0f}.")
                        st.rerun()
                    if reset_fg:
                        delete_inventory_opening(sel_fg)
                        st.success(f"✅ {sel_fg} reset to hardcoded default ({fg_defaults[sel_fg]:.0f}).")
                        st.rerun()

            st.markdown("---")
            st.markdown("**All finished-good opening balances**")
            fg_rows = []
            for canonical in fg_options:
                override = db_opening.get(canonical)
                fg_rows.append({
                    "Product": canonical,
                    "Opening Qty": override["qty"] if override else fg_defaults[canonical],
                    "Source": "Manual override" if override else "Default",
                })
            st.dataframe(pd.DataFrame(fg_rows), use_container_width=True, hide_index=True)

        with rm_sub:
            rm_options = list(RM_INVENTORY_OPENING.keys())
            st.caption(
                "Cement/GGBS stock is tracked separately per plant (Pipe Factory / Pole Factory) — "
                "each plant's opening qty defaults to 0 until physically counted and entered here; "
                "there's no meaningful way to auto-split the old single combined figure between them."
            )

            if can_edit:
                rmc1, rmc2 = st.columns(2)
                sel_rm = rmc1.selectbox(
                    "Select Material", rm_options, key="inv_open_rm_sel",
                    format_func=lambda k: INVENTORY_MATERIAL_LABELS.get(k, k),
                )
                sel_rm_plant = rmc2.selectbox("Plant", PLANTS, key="inv_open_rm_plant_sel")
                rm_item_key = f"{sel_rm}::{sel_rm_plant}"
                override_rm = db_opening.get(rm_item_key)
                current_rm_val = override_rm["qty"] if override_rm else 0.0

                with st.form("inv_open_rm_form"):
                    new_rm_val = st.number_input(
                        "Opening Qty (Bags)", value=float(current_rm_val), min_value=0.0, step=1.0,
                    )
                    if override_rm:
                        st.caption(f"Currently set to {override_rm['qty']:.0f} "
                                   f"by {override_rm['updated_by'] or 'unknown'} on {override_rm['updated_at']}.")
                    else:
                        st.caption("No physical count entered yet for this plant — defaults to 0.")
                    rc1, rc2 = st.columns(2)
                    save_rm_open = rc1.form_submit_button("💾 Save Opening Qty", type="primary", use_container_width=True)
                    reset_rm = rc2.form_submit_button("↩️ Reset to 0", use_container_width=True,
                                                       disabled=not override_rm)
                    if save_rm_open:
                        save_inventory_opening(rm_item_key, "raw_material", new_rm_val,
                                                st.session_state.get("username") or "")
                        st.success(f"✅ Opening qty for {INVENTORY_MATERIAL_LABELS.get(sel_rm, sel_rm)} "
                                   f"({sel_rm_plant}) set to {new_rm_val:.0f}.")
                        st.rerun()
                    if reset_rm:
                        delete_inventory_opening(rm_item_key)
                        st.success(f"✅ {INVENTORY_MATERIAL_LABELS.get(sel_rm, sel_rm)} ({sel_rm_plant}) reset to 0.")
                        st.rerun()

            st.markdown("---")
            st.markdown("**All raw material opening balances**")
            rm_rows = []
            for key in rm_options:
                for p in PLANTS:
                    override_rm = db_opening.get(f"{key}::{p}")
                    rm_rows.append({
                        "Material": INVENTORY_MATERIAL_LABELS.get(key, key),
                        "Plant": p,
                        "Opening Qty": override_rm["qty"] if override_rm else 0.0,
                        "Source": "Manual entry" if override_rm else "Not yet counted (0)",
                    })
            st.dataframe(pd.DataFrame(rm_rows), use_container_width=True, hide_index=True)

    # ── Tab 3: All Production ─────────────────────────────────────────────────
    with tab3:
        st.markdown("### All Production Records")
        c1, c2 = st.columns(2)
        from core.tz import today_ist
        start = c1.date_input("From", today_ist().replace(day=1), key="prod_start")
        end   = c2.date_input("To",   today_ist(), key="prod_end")

        df = get_production(str(start), str(end))
        if df.empty:
            st.info("No production records found.")
        else:
            st.markdown(f"**{len(df)} records** | "
                        f"Total Nos: {df['nos'].sum():,.0f} | "
                        f"Total Revenue: ₹{df['revenue'].sum()/LAKH:.2f}L | "
                        f"Total Profit: ₹{df['profit'].sum()/LAKH:.2f}L")

            df = add_ist_timestamp(df)
            df = df.rename(columns={"created_at": "Entered At"})
            st.dataframe(df, use_container_width=True, hide_index=True,
                         column_config={"Entered At": timestamp_col_config()})

            # Export
            csv = sanitize_for_export(df).to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Download CSV", csv,
                               f"production_{start}_{end}.csv", "text/csv")

            # Delete record
            if can_edit:
                st.markdown("---")
                del_id = st.number_input("Delete record by ID", min_value=1, step=1)
                if st.button("🗑️ Delete", type="secondary"):
                    delete_row("production", int(del_id))
                    st.success(f"Record {del_id} deleted.")
                    st.rerun()

        # ── Import from CSV ──────────────────────────────────────────────────
        if can_edit:
            st.markdown("---")
            with st.expander("⬆️ Import Production (DPR) from CSV"):
                st.caption(
                    "Required columns: **date, product, nos**. `product` must exactly match a product name "
                    "from DPR Entry (e.g. \"Hume Pipe 300mm NP3 (Socket & Spigot)\"). Plant is set "
                    "automatically from the product (Hume Pipes -> Pipe Factory, everything else -> "
                    "Pole Factory) — any **plant** column in the file is ignored. Costs are "
                    "auto-calculated the same way as a manual DPR entry."
                )
                prod_file = st.file_uploader("CSV file", type=["csv"], key="prod_import_file")
                if prod_file is not None:
                    try:
                        imp_df = pd.read_csv(prod_file)
                    except Exception as e:
                        st.error(f"Could not read CSV: {e}")
                        imp_df = None

                    if imp_df is not None:
                        imp_df.columns = [re.sub(r"[\s\-]+", "_", c.strip().lower()) for c in imp_df.columns]
                        missing = [c for c in ("date", "product", "nos") if c not in imp_df.columns]
                        if missing:
                            st.error(f"Missing required column(s): {', '.join(missing)}")
                        else:
                            bad_products = sorted(set(imp_df["product"].astype(str)) - set(PRODUCTION_PRODUCTS))
                            if bad_products:
                                st.error("Unknown product name(s) — must match DPR Entry exactly: "
                                          + ", ".join(bad_products))
                            else:
                                st.markdown(f"**Preview — {len(imp_df)} row(s)**")
                                st.dataframe(imp_df.head(20), use_container_width=True, hide_index=True)
                                if st.button(f"✅ Import {len(imp_df)} Production Row(s)", type="primary", key="prod_import_btn"):
                                    rm = get_rm_prices()
                                    prod_cfg_i = get_product_config()
                                    pipe_dia_cfg_i = get_pipe_diameter_config()
                                    imported = 0
                                    for _, r in imp_df.iterrows():
                                        nos = float(r["nos"])
                                        if nos <= 0:
                                            continue
                                        product = str(r["product"])
                                        plant = plant_for_product(product)
                                        pricing_key = SKU_TO_PRICING_KEY.get(product, product)
                                        r_date = str(pd.to_datetime(r["date"]).date())
                                        result = calculate_production(pricing_key, nos, rm, prod_cfg_i,
                                                                        pipe_diameter_config=pipe_dia_cfg_i)
                                        record = {
                                            "date": r_date,
                                            "product": product, "nos": nos, "plant": plant,
                                            **result,
                                        }
                                        insert_production(record)
                                        imported += 1
                                    st.success(f"✅ Imported {imported} production row(s).")
                                    st.rerun()

    # ── Tab 4: All Dispatch ───────────────────────────────────────────────────
    with tab4:
        st.markdown("### All Dispatch Records")

        # One-time: mark all existing null bill_no entries as billed
        if can_edit:
            from core.db import get_dispatch as _get_all_disp, update_dispatch as _upd_disp, _use_supabase, _sb_url, _headers
            import requests as _req
            df_null = _get_all_disp()
            if not df_null.empty:
                null_mask = df_null["bill_no"].isna() | (df_null["bill_no"].astype(str).str.strip().isin(["","None","nan"]))
                null_count = null_mask.sum()
                if null_count > 0:
                    st.warning(f"⚠️ **{null_count} existing entries** have no Bill No. (showing as pending invoice).")
                    if st.button(f"✅ Mark all {null_count} existing entries as BILLED (one-time)", key="mark_all_billed"):
                        if _use_supabase():
                            r = _req.patch(
                                f"{_sb_url('dispatch')}",
                                headers={**_headers(), "Prefer": "return=minimal"},
                                params={"bill_no": "is.null"},
                                json={"bill_no": "BILLED"},
                            )
                            if r.status_code in (200, 204):
                                st.success(f"✅ {null_count} entries marked as BILLED.")
                                st.rerun()
                            else:
                                st.error(f"Error: {r.text}")
                        else:
                            from core.db import _conn
                            con = _conn()
                            con.execute("UPDATE dispatch SET bill_no = 'BILLED' WHERE bill_no IS NULL OR bill_no = ''")
                            con.commit(); con.close()
                            st.success(f"✅ {null_count} entries marked as BILLED.")
                            st.rerun()
            st.markdown("---")
        c3, c4 = st.columns(2)
        from core.tz import today_ist
        start2 = c3.date_input("From", today_ist().replace(day=1), key="disp_start")
        end2   = c4.date_input("To",   today_ist(), key="disp_end")

        df2 = get_dispatch(str(start2), str(end2))
        if df2.empty:
            st.info("No dispatch records found.")
        else:
            total_val2 = df2["dispatch_value"].sum()
            zero_rows  = df2[(df2["dispatch_value"] == 0) | df2["dispatch_value"].isna()].copy()
            fixable    = df2[(df2.get("qty_dispatched", pd.Series([0]*len(df2))).fillna(0) > 0) &
                             (df2.get("rate", pd.Series([0]*len(df2))).fillna(0) > 0) &
                             ((df2["dispatch_value"].fillna(0) == 0) |
                              ((df2["dispatch_value"].fillna(0) - df2.get("qty_dispatched", pd.Series([0]*len(df2))).fillna(0) *
                                df2.get("rate", pd.Series([0]*len(df2))).fillna(0)).abs() > 1))].copy()

            st.markdown(f"**{len(df2)} challans** | "
                        f"Total Dispatched: {df2['qty_dispatched'].sum():,.0f} nos | "
                        f"Total Value: ₹{total_val2/LAKH:.2f}L")

            if not fixable.empty:
                st.warning(f"⚠️ **{len(fixable)} entries** have dispatch_value that doesn't match qty × rate. "
                           f"Missing value: ₹{(fixable['qty_dispatched'].fillna(0) * fixable['rate'].fillna(0) - fixable['dispatch_value'].fillna(0)).sum()/LAKH:.2f}L")
                with st.expander(f"👁️ Show {len(fixable)} mismatched entries"):
                    fix_disp = fixable[["id","date","client_name","product","qty_dispatched","rate","dispatch_value"]].copy()
                    fix_disp["correct_value"] = (fix_disp["qty_dispatched"].fillna(0) * fix_disp["rate"].fillna(0)).round(2)
                    fix_disp["difference"] = fix_disp["correct_value"] - fix_disp["dispatch_value"].fillna(0)
                    st.dataframe(fix_disp, use_container_width=True, hide_index=True)

                if can_edit and st.button("🔧 Recalculate & Fix All dispatch_value (qty × rate)", type="primary", key="fix_dv"):
                    fixed = 0
                    for _, row in fixable.iterrows():
                        correct = round(float(row.get("qty_dispatched", 0) or 0) *
                                        float(row.get("rate", 0) or 0), 2)
                        update_dispatch(int(row["id"]), {"dispatch_value": correct})
                        fixed += 1
                    st.success(f"✅ Fixed {fixed} entries. Refresh to see updated totals.")
                    st.rerun()

            df2 = add_ist_timestamp(df2)
            df2 = df2.rename(columns={"created_at": "Entered At"})
            st.dataframe(df2, use_container_width=True, hide_index=True,
                         column_config={"Entered At": timestamp_col_config()})

            csv2 = sanitize_for_export(df2).to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Download CSV", csv2,
                               f"dispatch_{start2}_{end2}.csv", "text/csv",
                               key="dl_disp")

            if can_edit:
                st.markdown("---")
                st.markdown("**Delete by ID**")
                del_id2 = st.number_input("Delete record by ID", min_value=1, step=1, key="del_disp")
                if st.button("🗑️ Delete Single Record", type="secondary"):
                    delete_row("dispatch", int(del_id2))
                    st.success(f"Record {del_id2} deleted.")
                    st.rerun()

                st.markdown("---")
                st.markdown("**🗑️ Bulk Delete — entire date range**")
                st.caption(f"This will delete ALL {len(df2)} dispatch records from {start2} to {end2} in one shot.")
                confirm_txt = st.text_input("Type DELETE to confirm", key="bulk_del_confirm")
                if st.button(f"🗑️ Delete ALL {len(df2)} records in range", type="primary", key="bulk_del_btn"):
                    if confirm_txt.strip() == "DELETE":
                        from core.db import delete_dispatch_range
                        delete_dispatch_range(str(start2), str(end2))
                        st.success(f"✅ Deleted {len(df2)} records ({start2} → {end2}). Now re-import fresh data.")
                        st.rerun()
                    else:
                        st.error("Type exactly DELETE to confirm.")

        # ── Import from CSV ──────────────────────────────────────────────────
        if can_edit:
            st.markdown("---")
            with st.expander("⬆️ Import Dispatch Challans from CSV"):
                st.caption(
                    "Required columns: **date, challan_no, product, qty_dispatched, rate**. Optional: "
                    "**di_no, bill_no, sale_type, client_name, delivery_address, qty_ordered, "
                    "trip_distance, truck_no, driver_name, remarks, form_filled_by, gst_applicable** "
                    "(yes/no — defaults to no). `product` must exactly match a Dispatch Entry product name, "
                    "`sale_type` must be one of: " + ", ".join(SALE_TYPES) + f" (defaults to \"{SALE_TYPES[0]}\")."
                )
                disp_file = st.file_uploader("CSV file", type=["csv"], key="disp_import_file")
                if disp_file is not None:
                    try:
                        dimp_df = pd.read_csv(disp_file)
                    except Exception as e:
                        st.error(f"Could not read CSV: {e}")
                        dimp_df = None

                    if dimp_df is not None:
                        dimp_df.columns = [re.sub(r"[\s\-]+", "_", c.strip().lower()) for c in dimp_df.columns]
                        missing = [c for c in ("date", "challan_no", "product", "qty_dispatched", "rate")
                                   if c not in dimp_df.columns]
                        if missing:
                            st.error(f"Missing required column(s): {', '.join(missing)}")
                        else:
                            bad_products = sorted(set(dimp_df["product"].astype(str)) - set(DISPATCH_PRODUCTS))
                            if bad_products:
                                st.error("Unknown product name(s) — must match Dispatch Entry exactly: "
                                          + ", ".join(bad_products))
                            else:
                                st.markdown(f"**Preview — {len(dimp_df)} row(s)**")
                                st.dataframe(dimp_df.head(20), use_container_width=True, hide_index=True)
                                if st.button(f"✅ Import {len(dimp_df)} Dispatch Row(s)", type="primary", key="disp_import_btn"):
                                    imported = 0
                                    for _, r in dimp_df.iterrows():
                                        qty_d = float(r["qty_dispatched"])
                                        rate  = float(r["rate"])
                                        if qty_d <= 0:
                                            continue
                                        sale_type_v = str(r["sale_type"]).strip() if "sale_type" in dimp_df.columns and pd.notna(r.get("sale_type")) and str(r.get("sale_type")).strip() else SALE_TYPES[0]
                                        gst_flag = str(r.get("gst_applicable", "")).strip().lower() in ("yes", "true", "1") if "gst_applicable" in dimp_df.columns else False
                                        base_value = dispatch_value(qty_d, rate)
                                        gst_amt, d_value = gst_split(base_value, gst_flag)

                                        def _opt(col):
                                            return str(r[col]) if col in dimp_df.columns and pd.notna(r.get(col)) else None

                                        record = {
                                            "date": str(pd.to_datetime(r["date"]).date()),
                                            "challan_no": str(r["challan_no"]), "di_no": _opt("di_no"),
                                            "bill_no": _opt("bill_no"), "sale_type": sale_type_v,
                                            "client_name": _opt("client_name"), "delivery_address": _opt("delivery_address"),
                                            "product": str(r["product"]),
                                            "qty_ordered": float(r["qty_ordered"]) if "qty_ordered" in dimp_df.columns and pd.notna(r.get("qty_ordered")) else qty_d,
                                            "qty_dispatched": qty_d, "rate": rate,
                                            "dispatch_value": d_value, "gst_applicable": gst_flag, "gst_amount": gst_amt,
                                            "trip_distance": float(r["trip_distance"]) if "trip_distance" in dimp_df.columns and pd.notna(r.get("trip_distance")) else 0.0,
                                            "truck_no": _opt("truck_no"), "driver_name": _opt("driver_name"),
                                            "remarks": _opt("remarks"), "form_filled_by": _opt("form_filled_by"),
                                        }
                                        insert_dispatch(record)
                                        imported += 1
                                    st.success(f"✅ Imported {imported} dispatch row(s).")
                                    st.rerun()

    # ── Tab 5: Merge Client Names ─────────────────────────────────────────────
    with tab5:
        st.markdown("### 🧩 Merge Duplicate Client Names")
        st.caption(
            "Fixes cases like **\"Frontage\"** vs **\"Frontage Construction\"** being counted as "
            "two different clients in Top-10-Clients analytics. Pick the variants below, choose "
            "(or type) the correct name, and every matching Sales Order + Dispatch record will be "
            "renamed to it. New entries now use a client dropdown (Sales Orders / Dispatch pages) "
            "to prevent this going forward."
        )

        if not can_edit:
            st.info("👁️ View-only — ask an Admin to merge client names.")
        else:
            df_ord_m  = get_orders()
            df_disp_m = get_dispatch()

            counts = {}
            if not df_ord_m.empty and "client_name" in df_ord_m.columns:
                for name, cnt in df_ord_m["client_name"].dropna().astype(str).value_counts().items():
                    counts[name] = counts.get(name, 0) + int(cnt)
            if not df_disp_m.empty and "client_name" in df_disp_m.columns:
                for name, cnt in df_disp_m["client_name"].dropna().astype(str).value_counts().items():
                    counts[name] = counts.get(name, 0) + int(cnt)

            all_names = sorted(n for n in counts if n.strip())
            if len(all_names) < 2:
                st.info("Not enough distinct client names yet to merge anything.")
            else:
                variants = st.multiselect(
                    "Select the name variants that are actually the same client",
                    all_names,
                    format_func=lambda n: f"{n}  ({counts.get(n, 0)} records)",
                    key="merge_variants",
                )

                if len(variants) >= 2:
                    target_options = variants + ["+ Type a different correct name"]
                    target_pick = st.selectbox("Correct name to use for all of these", target_options, key="merge_target_pick")
                    if target_pick == "+ Type a different correct name":
                        target = st.text_input("Correct client name", key="merge_target_new").strip()
                    else:
                        target = target_pick

                    affected_ord  = df_ord_m[df_ord_m["client_name"].astype(str).isin(variants)] if not df_ord_m.empty else pd.DataFrame()
                    affected_disp = df_disp_m[df_disp_m["client_name"].astype(str).isin(variants)] if not df_disp_m.empty else pd.DataFrame()
                    total_affected = len(affected_ord) + len(affected_disp)

                    st.warning(
                        f"This will rename **{total_affected} record(s)** "
                        f"({len(affected_ord)} order line(s), {len(affected_disp)} dispatch entr(y/ies)) "
                        f"to **\"{target or '—'}\"**."
                    )

                    confirm = st.text_input("Type MERGE to confirm", key="merge_confirm")
                    if st.button(f"🧩 Merge {total_affected} record(s)", type="primary", disabled=not target):
                        if confirm.strip() != "MERGE":
                            st.error("Type exactly MERGE to confirm.")
                        else:
                            n = 0
                            for _, row in affected_ord.iterrows():
                                if str(row["client_name"]) != target:
                                    update_order(int(row["id"]), {"client_name": target})
                                    n += 1
                            for _, row in affected_disp.iterrows():
                                if str(row["client_name"]) != target:
                                    update_dispatch(int(row["id"]), {"client_name": target})
                                    n += 1
                            st.success(f"✅ Merged {n} record(s) into \"{target}\".")
                            st.rerun()
                else:
                    st.caption("Select at least 2 name variants above to merge them.")

    # ── Tab 6: Activity Log ────────────────────────────────────────────────────
    with tab6:
        st.markdown("### Who opened / edited what")
        st.caption("Every login, page view, create, edit, and delete across the app.")

        df_log = get_activity_log()
        if df_log.empty:
            st.info("No activity recorded yet.")
        else:
            # Supabase's TIMESTAMPTZ comes back UTC; convert to IST before
            # stripping the tz so it can be compared against the tz-naive
            # From/To date inputs below — this used to just drop the tz
            # without converting, showing raw UTC time as if it were IST.
            df_log["created_at"] = (
                pd.to_datetime(df_log["created_at"], errors="coerce", utc=True)
                .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
            )
            df_log = df_log.sort_values(["created_at", "id"], ascending=[False, False])

            log_start, log_end = date_range_filter(
                "activity_log", default_start=df_log["created_at"].min().date()
            )
            df_log = df_log[(df_log["created_at"] >= pd.Timestamp(log_start)) &
                             (df_log["created_at"] <= pd.Timestamp(log_end) + pd.Timedelta(days=1))]

            show_cols = ["created_at", "name", "role", "action", "module", "detail"]
            show_cols = [c for c in show_cols if c in df_log.columns]
            rename = {
                "created_at": "When", "name": "User", "role": "Role",
                "action": "Action", "module": "Module", "detail": "Detail",
            }
            col_cfg = {"created_at": st.column_config.DatetimeColumn("When", format="DD-MMM-YYYY HH:mm")}
            interactive_table(df_log, key="activity_log", show_cols=show_cols, rename=rename, col_config=col_cfg)

    # ── Tab 7: Edit Requests ───────────────────────────────────────────────────
    with tab7:
        st.markdown("### Pending Edit Requests")
        st.caption(
            "Submitted by roles that can't edit directly (Production/Factory on DPR, Dispatch/Factory "
            "on Dispatch, Headoffice on Sales Orders). Approving applies the change to the live record "
            "immediately; rejecting discards it — nothing here touches real data until you decide."
        )

        df_reqs = get_edit_requests()
        if df_reqs.empty:
            st.info("No edit requests yet.")
        else:
            pending = df_reqs[df_reqs["status"] == "pending"].sort_values("created_at")
            if pending.empty:
                st.success("✅ No pending requests.")
            else:
                for _, req in pending.iterrows():
                    old = json.loads(req["old_data"]) if req.get("old_data") else {}
                    new = json.loads(req["new_data"]) if req.get("new_data") else {}
                    changed = {k: (old.get(k), new.get(k)) for k in new if str(old.get(k)) != str(new.get(k))}

                    header = f"{req['module_label']} — {req['summary']} · by {req.get('requested_by_name') or req.get('requested_by')}"
                    with st.expander(header):
                        st.caption(f"Submitted {req['created_at']} by {req.get('requested_by_name','')} "
                                  f"({req.get('requested_role','')})")
                        if changed:
                            diff_rows = [
                                {"Field": k.replace("_", " ").title(), "Current": v_old, "Requested": v_new}
                                for k, (v_old, v_new) in changed.items()
                            ]
                            st.dataframe(pd.DataFrame(diff_rows), use_container_width=True, hide_index=True)
                        else:
                            st.caption("No field-level changes detected.")

                        if can_edit:
                            ac1, ac2 = st.columns(2)
                            if ac1.button("✅ Approve", type="primary", key=f"appr_{req['id']}", use_container_width=True):
                                try:
                                    approve_edit_request(int(req["id"]))
                                    st.success("Approved and applied.")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Could not apply: {e}")
                            note_key = f"rej_note_{req['id']}"
                            ac2.text_input("Rejection note (optional)", key=note_key,
                                          label_visibility="collapsed", placeholder="Reason (optional)")
                            if ac2.button("❌ Reject", key=f"rej_{req['id']}", use_container_width=True):
                                reject_edit_request(int(req["id"]), st.session_state.get(note_key, ""))
                                st.success("Rejected.")
                                st.rerun()

            st.markdown("---")
            st.markdown("**Recent decisions**")
            decided = df_reqs[df_reqs["status"] != "pending"].sort_values("created_at", ascending=False).head(50)
            if decided.empty:
                st.caption("No decisions yet.")
            else:
                dd = decided[["created_at", "module_label", "summary", "status", "reviewed_by", "review_note"]].rename(columns={
                    "created_at": "Submitted", "module_label": "Module", "summary": "Entry",
                    "status": "Status", "reviewed_by": "Reviewed By", "review_note": "Note",
                })
                st.dataframe(dd, use_container_width=True, hide_index=True)

    # ── Tab 8: User Permissions ──────────────────────────────────────────────
    with tab8:
        st.markdown("### Per-User Permission Overrides")
        st.caption(
            "Access is role-based by default. Pick a user to override their "
            "View/Add/Edit/Delete rights for any module — rows left at their "
            "role default (pre-filled below) aren't saved as overrides; only "
            "cells you actually change are stored, so future role-default "
            "changes still apply to everything you haven't touched."
        )
        if not can_edit:
            st.info("👁️ View-only — only Admin can change permissions.")

        all_users = get_all_users()
        if not all_users:
            st.warning("No users configured — add them under `[users]` in `.streamlit/secrets.toml`.")
        else:
            user_labels = {f"{u} ({info.get('role','?')})": u for u, info in sorted(all_users.items())}
            sel_label = st.selectbox("Select user", list(user_labels.keys()), key="perm_user_sel")
            sel_user  = user_labels[sel_label]
            sel_role  = all_users[sel_user].get("role", "viewer")

            df_overrides = get_user_permissions()
            existing = {}
            if not df_overrides.empty:
                mine = df_overrides[df_overrides["username"] == sel_user]
                for _, r in mine.iterrows():
                    existing[r["module"]] = {
                        "view": bool(r["can_view"]), "add": bool(r["can_add"]),
                        "edit": bool(r["can_edit"]), "delete": bool(r["can_delete"]),
                    }

            _blank = {"view": False, "add": False, "edit": False, "delete": False}
            rows = []
            for m in MODULES:
                default = ROLE_DEFAULTS.get(sel_role, {}).get(m, _blank)
                eff = existing.get(m, default)
                rows.append({
                    "_module_key": m, "Module": MODULE_LABELS[m],
                    "View": eff["view"], "Add": eff["add"], "Edit": eff["edit"], "Delete": eff["delete"],
                    "Overridden": m in existing,
                })
            edit_df = pd.DataFrame(rows)

            st.caption(f"Role: **{sel_role}** — the ✅ under \"Overridden\" marks modules that already "
                       f"have a saved override for this user.")
            edited = st.data_editor(
                edit_df,
                key=f"perm_editor_{sel_user}",
                use_container_width=True, hide_index=True, disabled=not can_edit,
                column_order=["Module", "View", "Add", "Edit", "Delete", "Overridden"],
                column_config={
                    "_module_key": None,
                    "Module":     st.column_config.TextColumn("Module", disabled=True),
                    "View":       st.column_config.CheckboxColumn("View"),
                    "Add":        st.column_config.CheckboxColumn("Add"),
                    "Edit":       st.column_config.CheckboxColumn("Edit"),
                    "Delete":     st.column_config.CheckboxColumn("Delete"),
                    "Overridden": st.column_config.CheckboxColumn("Overridden", disabled=True),
                },
            )

            pc1, pc2 = st.columns(2)
            if can_edit and pc1.button("💾 Save Permissions", type="primary", key="perm_save_btn",
                                       use_container_width=True):
                saved, cleared = 0, 0
                for _, r in edited.iterrows():
                    m = r["_module_key"]
                    default = ROLE_DEFAULTS.get(sel_role, {}).get(m, _blank)
                    vals = {"view": bool(r["View"]), "add": bool(r["Add"]),
                            "edit": bool(r["Edit"]), "delete": bool(r["Delete"])}
                    if vals == default:
                        if m in existing:
                            delete_user_permission(sel_user, m)
                            cleared += 1
                    else:
                        save_user_permission(sel_user, m, vals["view"], vals["add"], vals["edit"], vals["delete"],
                                             st.session_state.get("username") or "")
                        saved += 1
                st.success(f"✅ Saved for {sel_user}: {saved} override(s) set, {cleared} reverted to role default.")
                st.rerun()

            if can_edit and existing and pc2.button("↩️ Reset All to Role Default", key="perm_reset_btn",
                                                     use_container_width=True):
                for m in list(existing.keys()):
                    delete_user_permission(sel_user, m)
                st.success(f"✅ All overrides cleared for {sel_user} — back to {sel_role} defaults.")
                st.rerun()

            st.markdown("---")
            st.markdown("**All active overrides (every user)**")
            if df_overrides.empty:
                st.caption("No per-user overrides set — everyone is on their role's default permissions.")
            else:
                disp = df_overrides.copy()
                disp["module"] = disp["module"].map(MODULE_LABELS).fillna(disp["module"])
                disp = disp.rename(columns={
                    "username": "User", "module": "Module", "can_view": "View", "can_add": "Add",
                    "can_edit": "Edit", "can_delete": "Delete", "updated_by": "Set By", "updated_at": "Updated At",
                })
                cols = [c for c in ["User","Module","View","Add","Edit","Delete","Set By","Updated At"] if c in disp.columns]
                st.dataframe(disp[cols], use_container_width=True, hide_index=True)
