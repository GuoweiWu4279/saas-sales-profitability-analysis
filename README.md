# FP&A Regional Profitability Review - APJ Loss Diagnostic
This project evaluates regional and product-level profitability for a SaaS business, with a focused diagnostic on APJ underperformance, to support pricing, product mix, and regional investment decisions.

## Executive Summary
- **Problem:** APJ region significantly underperforms relative to AMER and EMEA, with weak and in some cases negative profit margins.
- **Finding:** Losses are highly concentrated in a small subset of products that combine meaningful sales volume with structurally low margins.
- **Action:** Results support repricing, discount governance, and product mix rationalization in APJ to mitigate losses and improve regional contribution.

## Decisions This Analysis Supports
- Which APJ products should be repriced, deprioritized, or exited based on profit and margin contribution?
- How should leadership allocate incremental GTM investment across regions to maximize profitability?
- Where is tighter pricing and discount control required to prevent margin leakage?

## Project Overview
- This project analyzes SaaS sales transaction data to evaluate regional and product-level profitability.
- The objective is to identify underperforming regions, diagnose loss-driving products, and surface insights that support pricing, product, and regional strategy decisions.
- The workflow mirrors a real-world FP&A / Analytics process:
    Raw data → Aggregation & margin analysis (Python) → Executive-ready dashboards (Tableau)

## Dataset
- Source: Kaggle – AWS SaaS Sales Dataset
- Records: ~10,000 transactions
- Key fields: Product, Region, Sales, Profit, Quantity

## Analysis Performed
- Product-level profitability and profit margin analysis
- Region-level revenue and profit comparison
- Deep dive into APJ region product performance
- Use Tableau to create detailed presentation and analysis to allow reader quickly identify underperforming region and products. 

## Tools Used
- Python
- pandas
- CSV outputs (Tableau-ready)
- Tableau

## Key Insights
- Some high-revenue products generate low or negative profit margins
- APJ region significantly underperforms compared to AMER and EMEA
- Specific products drive losses within APJ despite strong global performance

## Outputs
- Cleaned and aggregated datasets are exported to the `output/` folder for visualization and BI tools.
- Graphs and Story created in `Tableau/` folder for visualization and presentation.
