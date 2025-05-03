#!/usr/bin/env python3

import json
import numpy as np
import pandas as pd
from itertools import product
import matplotlib.pyplot as plt

def compute_cost(split, venues, order_size, λo, λu, θ):
    executed, cash_spent = 0, 0.0
    for i, v in enumerate(venues):
        exe = min(split[i], v["ask_size"])
        executed += exe
        cash_spent += exe * v["ask"]
    underfill = max(order_size - executed, 0)
    overfill = max(executed - order_size, 0)
    return cash_spent + θ * (underfill + overfill) + λu * underfill + λo * overfill

def allocate(order_size, venues, λo, λu, θ, step=100):
    splits = [([], 0)]
    for v in venues:
        splits = [
            (alloc + [q], used + q)
            for alloc, used in splits
            for q in range(0, min(order_size - used, v["ask_size"]) + 1, step)
        ]
    best_split, best_cost = [], float("inf")
    for alloc, used in splits:
        if used == order_size:
            cost = compute_cost(alloc, venues, order_size, λo, λu, θ)
            if cost < best_cost:
                best_cost, best_split = cost, alloc
    return best_split

def get_venues(df):
    return [{"ask": row.ask_px_00, "ask_size": int(row.ask_sz_00)} for _, row in df.iterrows() if row.ask_sz_00 > 0]

def bps(smart, base):
    return 10000 * (base - smart) / base if base else 0.0

def plot_cumulative_cost(fills, fname="results.png", label="SOR"):
    if not fills:
        return
    fills = sorted(fills, key=lambda x: x[0])
    cum_q, cum_c, q, c = [0], [0.0], 0, 0.0
    for _, qty, px in fills:
        q += qty
        c += qty * px
        cum_q.append(q)
        cum_c.append(c)
    plt.figure(figsize=(8, 5))
    plt.step(cum_q, cum_c, where="post", marker="o", linewidth=1.5, label=label)
    plt.xlabel("Cumulative Shares Filled")
    plt.ylabel("Cumulative Cost ($)")
    plt.title("Cumulative Cost vs Shares Filled")
    plt.grid(True, which="both", linestyle="--", linewidth=0.3)
    plt.tight_layout()
    plt.legend()
    plt.savefig(fname)
    plt.close()

class BackTester:
    def __init__(self, df):
        df = df.sort_values(['ts_event', 'publisher_id']).groupby(['ts_event', 'publisher_id']).first().reset_index()
        self.snapshots = df.groupby('ts_event', sort=True)
        # 60-second buckets for TWAP/VWAP baselines
        first_ts = df["ts_event"].iloc[0]
        df["_bucket"] = ((df["ts_event"] - first_ts).dt.total_seconds() // 60).astype(int)
        self.df = df

    def run_sor(self, order_size, params, step=100, return_fills=False):
        remaining, cash = order_size, 0.0
        fills = []
        for ts, snap in self.snapshots:
            if remaining <= 0:
                break
            venues = get_venues(snap)
            if not venues:
                continue
            split = allocate(remaining, venues, *params, step=step)
            for qty, v in zip(split, venues):
                fill = min(qty, v['ask_size'])
                cash += fill * v['ask']
                fills.append((ts, fill, v['ask']))
                remaining -= fill
        filled = order_size - remaining
        avg = cash / filled if filled else 0
        if return_fills:
            return cash, avg, fills
        return cash, avg

    def run_best_ask(self, order_size):
        remaining, cash = order_size, 0.0
        fills = []
        for ts, snap in self.snapshots:
            if remaining <= 0:
                break
            venues = get_venues(snap)
            if venues:
                best = min(venues, key=lambda x: x['ask'])
                fill = min(best['ask_size'], remaining)
                cash += fill * best['ask']
                fills.append((ts, fill, best['ask']))
                remaining -= fill
        filled = order_size - remaining
        avg = cash / filled if filled else 0
        return cash, avg, fills

    def run_twap(self, order_size):
        n_buckets = self.df["_bucket"].nunique()
        per_bucket = order_size // n_buckets
        remaining = order_size
        cash, fills = 0.0, []
        for b in sorted(self.df["_bucket"].unique()):
            alloc_here = min(per_bucket, remaining)
            bucket_cash, bucket_fills, leftover = self._iter_bucket_best_ask(b, alloc_here)
            cash += bucket_cash
            fills.extend(bucket_fills)
            remaining -= (alloc_here - leftover)
            if remaining <= 0:
                break
        filled = order_size - remaining
        avg = cash / filled if filled else 0
        return cash, avg, fills

    def _iter_bucket_best_ask(self, bucket, target_qty):
        snap = self.df[self.df["_bucket"] == bucket]
        fills, cash = [], 0.0
        for ts, venue_df in snap.groupby("ts_event", sort=True):
            venues = get_venues(venue_df)
            if not venues or target_qty <= 0:
                continue
            best = min(venues, key=lambda v: v["ask"])
            fill = min(target_qty, best["ask_size"])
            if fill:
                cash += fill * best["ask"]
                fills.append((ts, fill, best["ask"]))
                target_qty -= fill
            if target_qty <= 0:
                break
        return cash, fills, target_qty

    def run_vwap(self, order_size):
        buckets = sorted(self.df["_bucket"].unique())
        n_buckets = len(buckets)
        per_bucket = order_size // n_buckets
        extra_last = order_size - per_bucket * n_buckets
        remaining = order_size
        cash_spent = 0.0
        fills = []
        for idx, b in enumerate(buckets):
            target = per_bucket + (extra_last if idx == n_buckets - 1 else 0)
            target = min(target, remaining)
            if target <= 0:
                break
            snap = self.df[self.df["_bucket"] == b]
            for ts, venue_df in snap.groupby("ts_event", sort=True):
                venues = get_venues(venue_df)
                if not venues or target <= 0:
                    continue
                total_sz = sum(v["ask_size"] for v in venues)
                if total_sz == 0:
                    continue
                allocs = [int(target * v["ask_size"] / total_sz) for v in venues]
                shortfall = target - sum(allocs)
                if shortfall > 0:
                    cheapest = min(range(len(venues)), key=lambda i: venues[i]["ask"])
                    allocs[cheapest] += shortfall
                for alloc, v in zip(allocs, venues):
                    fill = min(alloc, v["ask_size"], remaining)
                    if fill <= 0:
                        continue
                    cash_spent += fill * v["ask"]
                    fills.append((ts, fill, v["ask"]))
                    remaining -= fill
                    if remaining <= 0:
                        break
                if remaining <= 0:
                    break
        filled = order_size - remaining
        avg = cash_spent / filled if filled else 0
        return cash_spent, avg, fills
    
def add_synthetic_venues(
    df: pd.DataFrame,
    n_extra: int = 2,
    base_venue: int | None = None,
    tick: float = 0.01,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Clone n_extra additional venues from base_venue (default = first in df).
    Each clone gets:
      • a fresh publisher_id
      • ask prices nudged by ±1–2 ticks
      • ask sizes scaled 50–150 %
    Depth-1 bid/ask ladders are shifted in sync so the book stays valid.
    """
    rng = np.random.default_rng(seed)
    base_pid = base_venue or df["publisher_id"].iloc[0]

    clones = []
    max_pub = df["publisher_id"].max()
    for k in range(1, n_extra + 1):
        pid_new = max_pub + k
        d = df[df["publisher_id"] == base_pid].copy()
        d["publisher_id"] = pid_new

        # random ±1 or ±2 ticks around the original ask
        price_shift = rng.integers(-2, 3) * tick
        ask_cols = d.filter(regex=r"ask_px_").columns
        bid_cols = d.filter(regex=r"bid_px_").columns
        d[ask_cols] = d[ask_cols] + price_shift
        d[bid_cols] = d[bid_cols] + price_shift
        # scale sizes 0.5–1.5×
        size_scale = rng.uniform(0.5, 1.5)
        size_cols = d.filter(regex=r"_(sz|ct)_").columns
        d[size_cols] = (d[size_cols] * size_scale).astype(int).clip(lower=1)

        clones.append(d)

    return pd.concat([df, *clones], ignore_index=True).sort_values(
        ["ts_event", "publisher_id"]
    )

def main():
    df = pd.read_csv('files/l1_day.csv', parse_dates=['ts_event'])
    # df = add_synthetic_venues(df, n_extra=2, base_venue=None, tick=0.01, seed=42)
    bt = BackTester(df)
    grid = product([0.01, 0.05, 0.1], repeat=3)
    order_size = 5000

    best_cost, best_params, best_fills = float('inf'), None, []
    for params in grid:
        cost, avg, fills = bt.run_sor(order_size, params, return_fills=True)
        if cost < best_cost:
            best_cost, best_params, best_fills = cost, params, fills

    ba_cost, ba_avg, ba_fills = bt.run_best_ask(order_size)
    twap_cost, twap_avg, twap_fills = bt.run_twap(order_size)
    vwap_cost, vwap_avg, vwap_fills = bt.run_vwap(order_size)

    result = {
        "best_params": dict(zip(["lambda_over", "lambda_under", "theta_queue"], best_params)),
        "smart_order_router": {"total_cash_spent": best_cost, "average_fill_price": best_cost / order_size},
        "best_ask_baseline": {
            "total_cash_spent": ba_cost,
            "average_fill_price": ba_avg,
            "savings_bps": bps(best_cost, ba_cost)
        },
        "twap_baseline": {
            "total_cash_spent": twap_cost,
            "average_fill_price": twap_avg,
            "savings_bps": bps(best_cost, twap_cost)
        },
        "vwap_baseline": {
            "total_cash_spent": vwap_cost,
            "average_fill_price": vwap_avg,
            "savings_bps": bps(best_cost, vwap_cost)
        }
    }

    print(json.dumps(result, indent=2))
    plot_cumulative_cost(best_fills, fname="results.png")

if __name__ == "__main__":
    main()