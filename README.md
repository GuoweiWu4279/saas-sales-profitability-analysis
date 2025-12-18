# SaaS Sales Profitability Analysis
Profitability analysis of a SaaS sales dataset using **Python (pandas)** and **Tableau** to identify profit drivers by **region** and diagnose loss-driving **products** in APJ.

## Project Overview
- This project analyzes SaaS sales transaction data to evaluate regional and product-level profitability.
- The objective is to identify underperforming regions, diagnose loss-driving products, and surface insights that support pricing, product, and regional strategy decisions.
- The workflow mirrors a real-world FP&A / Analytics process:
    Raw data → Aggregation & margin analysis (Python) → Executive-ready dashboards (Tableau)

## Business Question
- Which regions and products are driving profitability, and where are losses concentrated?

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
