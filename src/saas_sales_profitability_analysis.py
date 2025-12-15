import pandas as pd
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 2000)

import os
os.makedirs("output", exist_ok=True)


#Data import and Simple Check
# Step 1: Load the Dataset
file_path = "Data/SaaS-Sales.csv"

df = pd.read_csv(file_path)

# Step 2: Show the first few rows
print("===== HEAD =====")
print(df.head())

# Step 3: Show basic info
print("\n===== INFO =====")
print(df.info())

# Step 4: Show shape (rows, columns)
print("\n===== SHAPE =====")
print(df.shape)

# Step 5: Show columns
print("\n===== COLUMNS =====")
print(df.columns.tolist())

#-------------------------------------------------------------------------------------------------

# First question & Analysis: Which products are the most and least profitable?
# Step 6: Analyze profitability by product
product_summary = (
    df.groupby("Product", as_index=False)
        .agg({
            "Sales": "sum",
            "Profit": "sum",
            "Quantity": "sum"
        })
)

# Calculate profit margin
product_summary["profit_margin_pct"] = product_summary["Profit"] / product_summary["Sales"] * 100
# Sort by Profit (from highest to lowest)
product_summary = product_summary.sort_values(by="Profit", ascending=False)

print("\n===== PRODUCT PROFITABILITY (TOP 10) =====")
print(product_summary.head(10))
print("\n===== PRODUCT PROFITABILITY (Worst 10) =====")
print(product_summary.tail(10))


#--------------------------------------------------------------------------------------------------
# Second question & Analysis: Region-Level Profitability Analysis
# Step 7: Analyze profitability by region
region_summary = (
    df.groupby("Region", as_index=False)
        .agg({
            "Sales": "sum",
            "Profit": "sum",
            "Quantity": "sum"
        })
)

# Calculate profit margin for each region
region_summary["profit_margin_pct"] = region_summary["Profit"]/region_summary["Sales"] * 100
# Sort by total profit
region_profit_sorted = region_summary.sort_values(by="Profit", ascending=False)

print("\n===== REGION PROFITABILITY (by Profit - TOP) =====")
print(region_profit_sorted.to_string())

# Sort by profit margin
region_margin_sorted = region_summary.sort_values(by="profit_margin_pct", ascending=False)
print("\n===== REGION PROFIT MARGIN (by Profit MAGIN PCT - TOP) =====")
print(region_margin_sorted.to_string())

# Region-Level Deep Analysis
# Step 8: Region x Product profitability (focus on APJ)
region_product_summary = (
    df.groupby(["Region", "Product"], as_index=False)
        .agg({
            "Sales": "sum",
            "Profit": "sum",
            "Quantity": "sum"
        })
)

# Calculate profit margin
region_product_summary["profit_margin_pct"] = (region_product_summary["Profit"] / region_product_summary["Sales"] * 100)
# Focus on APJ only
apj_products = region_product_summary[region_product_summary["Region"] == "APJ"]
#Basic scan of raw data
print("\n===== APJ PRODUCT SUMMARY (RAW) ======")
print(apj_products.to_string())
# 8.2 APJ Products sorted by total profit (worst first)
apj_products_by_profits = apj_products.sort_values(
    by="Profit",
    ascending=True
)
print("\n===== APJ PRODUCTS (by profit - WORST FIRST) =====")
print(apj_products_by_profits.to_string())
# 8.3 APJ Prodcuts sorted by profit margin (worst first)
apj_products_by_margins = apj_products.sort_values(
    by="profit_margin_pct",
    ascending=True
)
print("\n===== APJ PRODUCTS (by profit margin - WORST FIRST) =====")
print(apj_products_by_margins.to_string())

#---------------------------------------------------------------------------------------------------
# Step C: Export cleaned summary tables to Tableau for presentation and visualization

region_summary.to_csv("output/region_summary.csv", index=False)
product_summary.to_csv("output/product_summary.csv", index=False)
region_product_summary.to_csv("output/region_product_summary.csv", index=False)

print("\n===== CSV FILES EXPORTED TO TABLEAU =====")

