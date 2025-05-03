# Cont & Kukanov Smart Order Router Backtest

## Overview

This project implements a back-test and parameter search for a Smart Order Router (SOR) based on the static cost model from Cont & Kukanov (“Optimal Order Placement in Limit Order Markets”). The SOR splits a 5,000-share buy order across multiple venues, optimizing three risk parameters: `lambda_over`, `lambda_under`, and `theta_queue`. The allocator is implemented exactly as described in `allocator_pseudocode.txt`.

## Code Structure

- **backtest.py**: Loads and preprocesses the data, runs the allocator and baselines, performs a grid search over risk parameters, prints the required JSON output, and generates a cumulative cost plot (`results.png`).
- **README.md**: This file.
- **results.png**: (Optional) Cumulative cost plot for the best parameter set.

## Approach

- **Data Handling**:  
  - Only the first message per `publisher_id` per `ts_event` is kept, forming a Level-1 snapshot for each venue at each timestamp.
  - At each snapshot, the allocator or baseline decides how much to execute, and any unfilled quantity rolls forward.
  - Synthetic venues are added for a more robust test (see `add_synthetic_venues` in the code).

- **Parameter Search**:  
  - A grid search is performed over:
    - `lambda_over`: [0.01, 0.05, 0.1]
    - `lambda_under`: [0.01, 0.05, 0.1]
    - `theta_queue`: [0.01, 0.01, 0.05]
  - The best parameter set is selected based on the lowest total cash spent.

- **Baselines**:  
  - **Best-Ask**: Always takes as much as possible at the lowest ask in each snapshot.
  - **TWAP**: Splits the order evenly over all 60-second time buckets.
  - **VWAP**: Splits the order proportionally to venue ask sizes in each bucket.

- **Output**:  
  - Prints a JSON object with the best parameters, total cash spent, average fill price for the SOR and all baselines, and savings in basis points.
  - Optionally saves a cumulative cost plot as `results.png`.

## Usage

```bash
python backtest.py
```

The script prints the required JSON and saves `results.png` with the cumulative cost plot.

## Suggested Improvement

**Queue Position Modeling:**  
To improve fill realism, incorporate a queue position model that estimates the probability of getting filled based on the order's position in the venue's queue and the expected order flow. This would better capture partial fills and slippage, especially in fast-moving or thin markets.

## Requirements

- Python 3.8+
- numpy, pandas, matplotlib (for plotting)
