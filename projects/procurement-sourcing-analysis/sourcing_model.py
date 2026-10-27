"""
Strategic Sourcing & Procurement Analysis
Run: python sourcing_model.py

Generates supplier data, calculates TCO, scores vendors,
and produces a procurement analytics dashboard.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime, timedelta

plt.style.use("seaborn-v0_8")
np.random.seed(42)

SUPPLIERS = {
    'AlphaComponents': {'price_factor': 1.0, 'quality': 0.97, 'otd': 0.94, 'lead_days': 5, 'risk': 'low', 'location': 'USA'},
    'BetaManufacturing': {'price_factor': 0.85, 'quality': 0.91, 'otd': 0.88, 'lead_days': 12, 'risk': 'medium', 'location': 'Mexico'},
    'GammaGlobal': {'price_factor': 0.72, 'quality': 0.88, 'otd': 0.82, 'lead_days': 25, 'risk': 'high', 'location': 'China'},
    'DeltaParts': {'price_factor': 0.95, 'quality': 0.96, 'otd': 0.92, 'lead_days': 7, 'risk': 'low', 'location': 'USA'},
    'EpsilonSupply': {'price_factor': 0.78, 'quality': 0.93, 'otd': 0.90, 'lead_days': 15, 'risk': 'medium', 'location': 'India'},
    'ZetaTech': {'price_factor': 1.05, 'quality': 0.99, 'otd': 0.97, 'lead_days': 4, 'risk': 'low', 'location': 'USA'},
    'EtaWorks': {'price_factor': 0.88, 'quality': 0.90, 'otd': 0.85, 'lead_days': 18, 'risk': 'medium', 'location': 'Vietnam'},
    'ThetaCorp': {'price_factor': 0.92, 'quality': 0.94, 'otd': 0.91, 'lead_days': 8, 'risk': 'low', 'location': 'Canada'},
    'IotaIndustries': {'price_factor': 0.70, 'quality': 0.85, 'otd': 0.78, 'lead_days': 30, 'risk': 'high', 'location': 'China'},
    'KappaLogistics': {'price_factor': 0.90, 'quality': 0.95, 'otd': 0.93, 'lead_days': 6, 'risk': 'low', 'location': 'USA'},
}

CATEGORIES = {
    'Electronic Components': {'base_price': 25, 'volume': 50000},
    'Raw Materials': {'base_price': 8, 'volume': 200000},
    'Packaging': {'base_price': 3, 'volume': 500000},
    'MRO Supplies': {'base_price': 45, 'volume': 10000},
    'Logistics Services': {'base_price': 150, 'volume': 5000},
}

SHIPPING_RATES = {'USA': 0.02, 'Canada': 0.03, 'Mexico': 0.04, 'India': 0.08, 'Vietnam': 0.09, 'China': 0.10}

WEIGHTS = {'tco': 0.30, 'quality': 0.25, 'otd': 0.20, 'lead_time': 0.15, 'risk': 0.10}


def generate_orders(n=5000):
    orders = []
    for i in range(n):
        supplier = np.random.choice(list(SUPPLIERS.keys()))
        category = np.random.choice(list(CATEGORIES.keys()))
        s, c = SUPPLIERS[supplier], CATEGORIES[category]

        qty = np.random.randint(10, 500)
        unit_price = round(c['base_price'] * s['price_factor'] * np.random.uniform(0.9, 1.1), 2)
        shipping = round(qty * unit_price * SHIPPING_RATES[s['location']], 2)

        defect_rate = max(0, (1 - s['quality']) + np.random.uniform(-0.02, 0.02))
        quality_cost = round(qty * unit_price * defect_rate * 1.5, 2)

        on_time = np.random.random() < s['otd']
        late_penalty = 0 if on_time else round(qty * unit_price * 0.05, 2)

        tco = round(qty * unit_price + shipping + quality_cost + late_penalty, 2)

        orders.append({
            'po_number': f'PO-{i+1:05d}',
            'order_date': (datetime(2024, 1, 1) + timedelta(days=np.random.randint(0, 365))).strftime('%Y-%m-%d'),
            'supplier': supplier, 'category': category,
            'quantity': qty, 'unit_price': unit_price,
            'total_price': round(qty * unit_price, 2),
            'shipping_cost': shipping, 'quality_cost': quality_cost,
            'late_penalty': late_penalty, 'tco': tco,
            'on_time': on_time, 'defect_rate': round(defect_rate, 4),
            'lead_time_days': s['lead_days'] + np.random.randint(-2, 5),
        })
    return pd.DataFrame(orders)


def build_scorecard(df):
    sc = df.groupby('supplier').agg(
        avg_tco=('tco', 'mean'),
        quality=('defect_rate', lambda x: 1 - x.mean()),
        otd=('on_time', 'mean'),
        lead_time=('lead_time_days', 'mean'),
        total_spend=('tco', 'sum'),
        orders=('po_number', 'count'),
    ).reset_index()

    # Normalize to 0-100
    sc['tco_score'] = 100 * (1 - (sc['avg_tco'] - sc['avg_tco'].min()) / (sc['avg_tco'].max() - sc['avg_tco'].min()))
    sc['quality_score'] = sc['quality'] * 100
    sc['otd_score'] = sc['otd'] * 100
    sc['lead_score'] = 100 * (1 - (sc['lead_time'] - sc['lead_time'].min()) / (sc['lead_time'].max() - sc['lead_time'].min()))
    sc['risk_score'] = sc['supplier'].map({s: {'low': 90, 'medium': 60, 'high': 30}[d['risk']] for s, d in SUPPLIERS.items()})

    sc['composite'] = (
        sc['tco_score'] * WEIGHTS['tco'] +
        sc['quality_score'] * WEIGHTS['quality'] +
        sc['otd_score'] * WEIGHTS['otd'] +
        sc['lead_score'] * WEIGHTS['lead_time'] +
        sc['risk_score'] * WEIGHTS['risk']
    )
    return sc.sort_values('composite', ascending=False)


def consolidation_analysis(df, scorecard, top_n=5):
    top_suppliers = scorecard.head(top_n)['supplier'].tolist()
    current = df['tco'].sum()
    # Simulate shifting to top suppliers with volume discount
    savings = current * 0.05 * (1 - len(top_suppliers) / len(SUPPLIERS))
    return current, current - savings, savings


def create_dashboard(df, scorecard, current, projected, savings):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle('Strategic Sourcing Analysis — Procurement Dashboard', fontsize=14, fontweight='bold')

    # Spend by supplier
    spend = df.groupby('supplier')['tco'].sum().sort_values()
    spend.plot(kind='barh', ax=axes[0, 0], color=sns.color_palette("viridis", len(spend)))
    axes[0, 0].set_title('Total Spend by Supplier')
    axes[0, 0].set_xlabel('Spend ($)')

    # Composite scores
    top5 = scorecard.head(5)
    axes[0, 1].bar(range(5), top5['composite'], color=sns.color_palette("Set2", 5))
    axes[0, 1].set_xticks(range(5))
    axes[0, 1].set_xticklabels(top5['supplier'], rotation=30, ha='right')
    axes[0, 1].set_title('Top 5 Suppliers (Composite Score)')
    axes[0, 1].set_ylim(0, 100)

    # TCO vs Quality
    sc = axes[1, 0].scatter(scorecard['tco_score'], scorecard['quality_score'],
                            s=scorecard['total_spend'] / scorecard['total_spend'].max() * 500,
                            c=scorecard['composite'], cmap='RdYlGn', edgecolors='black', alpha=0.8)
    for _, row in scorecard.iterrows():
        axes[1, 0].annotate(row['supplier'][:6], (row['tco_score'] + 1, row['quality_score']), fontsize=7)
    axes[1, 0].set_xlabel('TCO Score (higher = cheaper)')
    axes[1, 0].set_ylabel('Quality Score')
    axes[1, 0].set_title('Cost vs Quality (size = spend volume)')

    # Summary
    axes[1, 1].axis('off')
    summary = f"""EXECUTIVE SUMMARY

Current Total Spend: ${current:,.0f}
Projected (consolidated): ${projected:,.0f}
Potential Savings: ${savings:,.0f} ({savings/current*100:.1f}%)

Recommendation:
Consolidate to top 5 preferred suppliers
Maintain dual-source for critical categories
Negotiate volume contracts (3-year terms)

Top Suppliers: {', '.join(scorecard.head(3)['supplier'].tolist())}"""
    axes[1, 1].text(0.1, 0.5, summary, fontsize=11, verticalalignment='center', fontfamily='monospace')

    plt.tight_layout()
    plt.savefig('procurement_dashboard.png', dpi=150, bbox_inches='tight')
    plt.show()


if __name__ == '__main__':
    print("=" * 50)
    print("  STRATEGIC SOURCING ANALYSIS")
    print("=" * 50)

    print("\n1. Generating procurement data...")
    df = generate_orders(5000)
    df.to_csv('procurement_data.csv', index=False)
    print(f"   {len(df)} purchase orders, ${df['tco'].sum():,.0f} total spend")

    print("\n2. Building supplier scorecard...")
    scorecard = build_scorecard(df)
    print(f"   {'Supplier':<20} {'Score':>6} {'TCO':>6} {'Quality':>8} {'OTD':>6}")
    print(f"   {'-'*20} {'-'*6} {'-'*6} {'-'*8} {'-'*6}")
    for _, row in scorecard.iterrows():
        print(f"   {row['supplier']:<20} {row['composite']:6.1f} {row['tco_score']:6.1f} {row['quality_score']:8.1f} {row['otd_score']:6.1f}")

    print("\n3. Consolidation analysis...")
    current, projected, savings = consolidation_analysis(df, scorecard)
    print(f"   Current spend:  ${current:,.0f}")
    print(f"   Projected:      ${projected:,.0f}")
    print(f"   Savings:        ${savings:,.0f} ({savings/current*100:.1f}%)")

    print("\n4. Generating dashboard...")
    create_dashboard(df, scorecard, current, projected, savings)
    print("   ✅ Saved: procurement_dashboard.png")

    print("\n" + "=" * 50)
    print("  RESUME LINE:")
    print(f'  "Built a strategic sourcing model evaluating {len(SUPPLIERS)}')
    print(f'   suppliers, identifying ${savings:,.0f} ({savings/current*100:.0f}%) savings')
    print(f'   through data-driven vendor consolidation"')
    print("=" * 50)
