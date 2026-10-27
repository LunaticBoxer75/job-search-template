"""
Generate realistic supply chain data for Tableau dashboard project.
Run: python generate_data.py

Creates supply_chain_data.csv with 100K+ order records.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

np.random.seed(42)

N_ORDERS = 100000

print("Generating supply chain dataset...")

# Date range: 2 years
start_date = datetime(2023, 1, 1)
order_dates = [start_date + timedelta(days=np.random.randint(0, 730)) for _ in range(N_ORDERS)]

# Suppliers with different performance levels
suppliers = {
    "Acme Manufacturing": {"otif_base": 0.92, "lead_mean": 4.5},
    "GlobalParts Inc": {"otif_base": 0.88, "lead_mean": 5.2},
    "QuickShip Co": {"otif_base": 0.95, "lead_mean": 3.8},
    "BudgetSupply Ltd": {"otif_base": 0.78, "lead_mean": 6.5},
    "PremiumGoods Corp": {"otif_base": 0.94, "lead_mean": 4.0},
    "ReliableSource": {"otif_base": 0.96, "lead_mean": 3.5},
}

# Product categories with cost ranges
categories = {
    "Electronics": (50, 500),
    "Clothing": (10, 100),
    "Food & Beverage": (5, 50),
    "Industrial Parts": (20, 300),
    "Office Supplies": (3, 80),
    "Pharmaceuticals": (15, 200),
}

regions = ["West", "East", "Central", "South"]
shipping_modes = ["Standard", "Express", "Same Day"]

# Generate orders
data = []
for i in range(N_ORDERS):
    order_date = order_dates[i]
    supplier_name = np.random.choice(list(suppliers.keys()))
    supplier = suppliers[supplier_name]
    category = np.random.choice(list(categories.keys()))
    region = np.random.choice(regions, p=[0.35, 0.30, 0.20, 0.15])
    ship_mode = np.random.choice(shipping_modes, p=[0.60, 0.30, 0.10])

    # Lead time depends on supplier and shipping mode
    mode_factor = {"Standard": 1.0, "Express": 0.6, "Same Day": 0.2}[ship_mode]
    lead_days = max(1, int(np.random.normal(supplier["lead_mean"] * mode_factor, 1.5)))

    ship_date = order_date + timedelta(days=max(1, int(lead_days * 0.3)))
    delivery_date = order_date + timedelta(days=lead_days)

    # Promise date (usually lead_mean - 1 day)
    promised_days = max(2, int(supplier["lead_mean"] * mode_factor - 0.5))
    promised_date = order_date + timedelta(days=promised_days)

    # On-time determination
    is_on_time = delivery_date <= promised_date
    # Adjust with supplier's base OTIF rate (some randomness)
    if np.random.random() > supplier["otif_base"]:
        is_on_time = False
        delivery_date = promised_date + timedelta(days=np.random.randint(1, 4))

    # In-full determination (90-98% depending on supplier)
    in_full_rate = supplier["otif_base"] + 0.03
    is_in_full = np.random.random() < min(in_full_rate, 0.98)

    # Quantity and cost
    quantity = np.random.randint(1, 50)
    cost_range = categories[category]
    unit_cost = round(np.random.uniform(*cost_range), 2)
    shipping_cost = round(
        {"Standard": 8, "Express": 18, "Same Day": 35}[ship_mode]
        * (1 + quantity * 0.02) * np.random.uniform(0.8, 1.2), 2
    )

    data.append({
        "order_id": f"ORD-{i+1:06d}",
        "order_date": order_date.strftime("%Y-%m-%d"),
        "ship_date": ship_date.strftime("%Y-%m-%d"),
        "delivery_date": delivery_date.strftime("%Y-%m-%d"),
        "promised_date": promised_date.strftime("%Y-%m-%d"),
        "customer_region": region,
        "product_category": category,
        "supplier": supplier_name,
        "shipping_mode": ship_mode,
        "quantity": quantity,
        "unit_cost": unit_cost,
        "shipping_cost": shipping_cost,
        "on_time": "Yes" if is_on_time else "No",
        "in_full": "Yes" if is_in_full else "No",
    })

df = pd.DataFrame(data)
df.to_csv("supply_chain_data.csv", index=False)

print(f"\n✅ Generated: supply_chain_data.csv")
print(f"   Rows: {len(df):,}")
print(f"   Date range: {df['order_date'].min()} to {df['order_date'].max()}")
print(f"\n   Summary:")
print(f"   OTIF Rate: {(((df['on_time']=='Yes') & (df['in_full']=='Yes')).mean()*100):.1f}%")
print(f"   On-Time Rate: {(df['on_time']=='Yes').mean()*100:.1f}%")
print(f"   In-Full Rate: {(df['in_full']=='Yes').mean()*100:.1f}%")
print(f"   Avg Lead Time: {(pd.to_datetime(df['delivery_date']) - pd.to_datetime(df['order_date'])).dt.days.mean():.1f} days")
print(f"\n   Open supply_chain_data.csv in Tableau Public to begin!")
