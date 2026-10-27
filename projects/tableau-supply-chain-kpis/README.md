# Supply Chain KPI Dashboard in Tableau

**Skills Developed:** Tableau, Data Visualization, Supply Chain KPIs, Dashboard Design
**Difficulty:** Beginner (no prior Tableau experience needed)
**Duration:** 5-7 days (~2 hours/day)
**Resume Line:** "Designed an interactive Tableau dashboard tracking 8 supply chain KPIs (OTIF, fill rate, lead time, cost-per-order) across 100K+ orders, published to Tableau Public for stakeholder self-service"

---

## What You'll Build

An interactive supply chain performance dashboard with:
- KPI summary cards (big numbers with trend indicators)
- On-Time In-Full (OTIF) delivery rate by region
- Lead time distribution and trends
- Supplier performance scorecard
- Cost breakdown by shipping mode
- Interactive filters (date range, region, product category)

## Why This Project Matters

- **Tableau** appears in 35%+ of supply chain/analyst job postings
- Demonstrates you can communicate data visually to stakeholders
- Published dashboards on Tableau Public serve as a live portfolio
- Directly relevant to supply chain reporting and S&OP presentations

---

## Prerequisites

- A computer (Mac or Windows)
- Internet connection
- No prior Tableau experience needed
- ~2 hours per day for 5-7 days

---

## Step 1: Get Tableau Public (Free)

### Download
1. Go to: https://public.tableau.com/en-us/s/download
2. Click "Download Tableau Public" (the FREE version)
3. Create an account (you'll need this to save/publish)
4. Install and open Tableau Public

### What's the difference from Tableau Desktop?
- Tableau Public = FREE, saves to cloud (publicly visible)
- Tableau Desktop = Paid ($70/mo), saves locally
- For a portfolio project, Public is PERFECT — recruiters can see your live dashboard!

---

## Step 2: Get Supply Chain Data

### Option A: Use the provided dataset generator
Run this Python script to create a realistic supply chain dataset:

```bash
cd tableau-supply-chain-kpis   # the folder you downloaded
python generate_data.py
```

This creates `supply_chain_data.csv` with 100K+ order records.

### Option B: Download from Kaggle
- DataCo Smart Supply Chain: https://www.kaggle.com/datasets/shashwatwork/dataco-smart-supply-chain-for-big-data-analysis
- Supply Chain Shipment Pricing: https://www.kaggle.com/datasets/divyeshardeshana/supply-chain-shipment-pricing-data

### What the data looks like

| Column | Description | Example |
|--------|-------------|---------|
| order_id | Unique order ID | ORD-00001 |
| order_date | When order was placed | 2024-03-15 |
| ship_date | When order shipped | 2024-03-17 |
| delivery_date | When delivered | 2024-03-20 |
| promised_date | When it was promised | 2024-03-19 |
| customer_region | Geography | West, East, Central, South |
| product_category | What was ordered | Electronics, Clothing, Food |
| supplier | Who supplied it | Supplier A, B, C, D |
| shipping_mode | How it shipped | Standard, Express, Same Day |
| quantity | Units ordered | 1-100 |
| unit_cost | Cost per unit | $5-$500 |
| shipping_cost | Delivery cost | $3-$50 |
| on_time | Was it on time? | Yes/No |
| in_full | Was order complete? | Yes/No |

---

## Step 3: Connect Data in Tableau

1. Open Tableau Public
2. On the start screen, under **Connect**, click **Text file**
3. Navigate to `supply_chain_data.csv`
4. Tableau shows a preview of your data — verify columns look right
5. Click **Sheet 1** at the bottom to start building

### Understanding the Tableau Interface
- **Left panel** = your data fields (dimensions + measures)
- **Columns/Rows shelves** = where you drag fields to build charts
- **Marks card** = controls color, size, shape, labels
- **Filters shelf** = add interactive filters
- **Show Me** panel (top right) = suggests chart types

---

## Step 4: Build KPI Calculations

### Calculated Fields (right-click any field → Create Calculated Field)

**1. OTIF Rate (On-Time In-Full)**
```
Name: OTIF Rate
Formula: SUM(IF [On Time] = "Yes" AND [In Full] = "Yes" THEN 1 ELSE 0 END) / COUNT([Order Id])
```

**2. On-Time Rate**
```
Name: On-Time Rate
Formula: SUM(IF [On Time] = "Yes" THEN 1 ELSE 0 END) / COUNT([Order Id])
```

**3. Lead Time (Days)**
```
Name: Lead Time Days
Formula: DATEDIFF('day', [Order Date], [Delivery Date])
```

**4. Late Days**
```
Name: Late Days
Formula: DATEDIFF('day', [Promised Date], [Delivery Date])
```

**5. Total Cost**
```
Name: Total Cost
Formula: [Quantity] * [Unit Cost] + [Shipping Cost]
```

**6. Cost Per Order**
```
Name: Cost Per Order
Formula: SUM([Total Cost]) / COUNTD([Order Id])
```

---

## Step 5: Build Worksheets

### Sheet 1: KPI Summary Cards
1. Create a new worksheet, name it "KPIs"
2. Drag `OTIF Rate` to Text on the Marks card
3. Format as percentage (right-click → Format → Number → Percentage)
4. Make text LARGE (Format → Font → 36pt, Bold)
5. Add a label above: "OTIF Rate"
6. Duplicate this sheet for: On-Time Rate, Avg Lead Time, Total Orders

### Sheet 2: OTIF by Region (Bar Chart)
1. Drag `Customer Region` to Columns
2. Drag `OTIF Rate` to Rows
3. Drag `OTIF Rate` to Color (marks card)
4. Edit colors: Green for high (>90%), Red for low (<80%)
5. Add labels (drag OTIF Rate to Label)
6. Title: "Delivery Performance by Region"

### Sheet 3: Lead Time Trend (Line Chart)
1. Drag `Order Date` to Columns (right-click → Month)
2. Drag `AVG(Lead Time Days)` to Rows
3. Add a reference line at the target (e.g., 5 days)
4. Title: "Average Lead Time Trend"

### Sheet 4: Supplier Scorecard (Heatmap)
1. Drag `Supplier` to Rows
2. Drag `OTIF Rate` to Columns
3. Drag `On-Time Rate` to Columns (next to OTIF)
4. Drag `AVG(Lead Time Days)` to Columns
5. Use Color to highlight best/worst performers
6. Title: "Supplier Performance Scorecard"

### Sheet 5: Cost by Shipping Mode (Pie/Donut)
1. Drag `Shipping Mode` to Color
2. Drag `SUM(Total Cost)` to Angle (if pie) or Size
3. Add labels showing % of total
4. Title: "Shipping Cost Breakdown"

---

## Step 6: Assemble the Dashboard

1. Click **New Dashboard** tab (bottom of screen)
2. Set size: **Automatic** or **Fixed width: 1200px**
3. Drag worksheets from the left panel onto the canvas:
   - TOP: 4 KPI cards in a row (use a Horizontal container)
   - MIDDLE LEFT: OTIF by Region bar chart
   - MIDDLE RIGHT: Lead Time trend line
   - BOTTOM LEFT: Supplier scorecard
   - BOTTOM RIGHT: Cost breakdown
4. Add a **Filter**: Drag `Order Date` as a filter → Show filter → Select "Range of Dates"
5. Add title: "Supply Chain Performance Dashboard"

### Design Tips
- Use 2-3 colors max (blue for metrics, red for alerts, gray for neutral)
- Put the most important KPIs at the top-left (OTIF is #1)
- Add tooltips (hover text) with extra detail
- Keep it clean — white space is good
- Add a source note: "Data: 100K orders, Jan 2023 - Dec 2024"

---

## Step 7: Publish to Tableau Public

1. **File → Save to Tableau Public**
2. Log in with your Tableau Public account
3. Give it a name: "Supply Chain KPI Dashboard - [Your Name]"
4. Click Publish

Your dashboard is now LIVE at a public URL like:
`https://public.tableau.com/app/profile/yourname/viz/SupplyChainKPIDashboard`

**Add this URL to:**
- Your resume (under Projects)
- Your LinkedIn profile (Featured section)
- Job applications ("See my portfolio")

---

## Step 8: What to Put on Your Resume

### Resume Bullet Point:
> Designed an interactive Tableau dashboard tracking 8 supply chain KPIs (OTIF, fill rate, lead time, cost-per-order) across 100K+ orders with drill-down by region, supplier, and shipping mode. Published to Tableau Public for stakeholder self-service analytics.

### Interview Talking Points:
1. "I chose OTIF as the primary metric because it combines on-time AND in-full — a partial delivery that arrives late fails both dimensions."
2. "The supplier scorecard revealed that Supplier C had 15% lower OTIF than others, which I'd flag for a quarterly business review."
3. "I designed the dashboard for self-service: stakeholders can filter by date range and region without needing analyst support."
4. "The lead time trend showed a seasonal pattern — Q4 lead times increase 20% due to volume, which informs safety stock decisions."

---

## Glossary of Supply Chain KPIs

| KPI | Formula | Good Benchmark |
|-----|---------|----------------|
| OTIF | Orders on-time AND in-full / Total orders | >95% |
| On-Time Rate | Orders delivered by promised date / Total | >97% |
| Fill Rate | Lines shipped complete / Total lines | >98% |
| Lead Time | Days from order to delivery | <5 days |
| Perfect Order Rate | Orders with no issues / Total | >90% |
| Cost Per Order | Total logistics cost / Number of orders | Varies |
| Inventory Turns | COGS / Average Inventory | 8-12x |
| Supplier Defect Rate | Defective units / Total received | <1% |
