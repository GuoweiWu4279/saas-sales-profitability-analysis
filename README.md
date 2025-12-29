# FP&A Regional Profitability Review – APJ Loss Diagnostic

This project evaluates regional and product-level profitability for a SaaS business, with a focused diagnostic on APJ underperformance, to support pricing, product mix, and regional investment decisions.

## Executive Summary
- **Problem:** APJ region significantly underperforms relative to AMER and EMEA, with materially lower profit margins.
- **Finding:** APJ losses are highly concentrated — the top 2 loss-driving products account for ~78% of total regional losses, indicating a structurally concentrated loss profile rather than broad-based underperformance.
- **Action:** Results support targeted repricing, discount governance, and product mix rationalization for a small subset of APJ products to materially improve regional contribution.

## Decisions This Analysis Supports
- Which APJ products should be repriced, deprioritized, or exited based on loss contribution and unit economics?
- How should leadership allocate incremental GTM investment across regions to maximize profitability?
- Where is tighter pricing and discount control required to prevent margin leakage?

## Project Overview
- This project analyzes SaaS sales transaction data to evaluate regional and product-level profitability.
- The objective is to identify underperforming regions, diagnose loss-driving products, and surface insights that support pricing, product, and regional strategy decisions.
- The workflow mirrors a real-world FP&A process:  
  Raw data → Aggregation & margin diagnostics (Python) → Executive-ready dashboards (Tableau)

## Dataset
- Source: Kaggle – AWS SaaS Sales Dataset
- Records: ~10,000 transactions
- Key fields: Product, Region, Sales, Profit, Quantity

## Analysis Performed
- Product-level profitability and profit margin analysis
- Region-level revenue and profit comparison
- APJ loss concentration analysis using profit contribution and Pareto logic
- Tableau dashboards to surface executive-level insights

## Tools Used
- Python (pandas)
- CSV outputs (Tableau-ready)
- Tableau

## Key Insights
- APJ underperformance is driven by a small number of structurally unprofitable products
- Several loss-driving products exhibit negative unit economics despite meaningful sales volume
- Targeted interventions can address the majority of regional losses without broad portfolio changes

## Outputs
- Cleaned and aggregated datasets exported to the `output/` folder for BI and visualization
- Tableau dashboards and story for executive review
