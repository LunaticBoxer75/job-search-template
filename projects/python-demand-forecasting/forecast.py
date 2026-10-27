"""
Supply Chain Demand Forecasting — Complete Script
Run: python forecast.py

This is the same code from the README, packaged as a single runnable script.
It generates the dataset, trains models, and outputs the dashboard image.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error

plt.style.use("seaborn-v0_8")
sns.set_palette("husl")


def generate_demand_data():
    """Generate 3 years of realistic supply chain demand data."""
    np.random.seed(42)
    dates = pd.date_range("2022-01-01", "2024-12-31", freq="D")
    n = len(dates)

    seasonal = 30 * np.sin(2 * np.pi * np.arange(n) / 365 - np.pi / 2)
    dow_effect = np.array([0, 5, 10, 10, 5, -20, -25] * (n // 7 + 1))[:n]
    trend = np.arange(n) * 0.05
    noise = np.random.normal(0, 15, n)

    demand = 200 + seasonal + dow_effect + trend + noise
    demand = np.maximum(demand, 20)

    return pd.DataFrame({
        "date": dates,
        "demand": demand.round(0).astype(int),
        "price": np.round(np.random.uniform(18, 28, n), 2),
        "promotion": np.random.choice([0, 1], n, p=[0.85, 0.15]),
        "temperature": 50 + 25 * np.sin(2 * np.pi * np.arange(n) / 365) + np.random.normal(0, 5, n),
    })


def add_features(df):
    """Add time-based and lag features."""
    df["day_of_week"] = df["date"].dt.dayofweek
    df["month"] = df["date"].dt.month
    df["quarter"] = df["date"].dt.quarter
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["lag_1"] = df["demand"].shift(1)
    df["lag_7"] = df["demand"].shift(7)
    df["lag_30"] = df["demand"].shift(30)
    df["rolling_7_mean"] = df["demand"].rolling(7).mean()
    df["rolling_30_mean"] = df["demand"].rolling(30).mean()
    return df.dropna()


def train_model(df):
    """Train Random Forest model."""
    features = [
        "day_of_week", "month", "quarter", "is_weekend", "price",
        "promotion", "temperature", "lag_1", "lag_7", "lag_30",
        "rolling_7_mean", "rolling_30_mean",
    ]

    train = df[df["date"] < "2024-10-01"]
    test = df[df["date"] >= "2024-10-01"]

    X_train, y_train = train[features], train["demand"]
    X_test, y_test = test[features], test["demand"]

    model = RandomForestRegressor(n_estimators=200, max_depth=15, min_samples_leaf=5, random_state=42, n_jobs=-1)
    model.fit(X_train, y_train)

    predictions = model.predict(X_test)
    mae = mean_absolute_error(y_test, predictions)
    mape = (abs(y_test.values - predictions) / y_test.values).mean() * 100

    return model, test, y_test, predictions, mae, mape, features


def create_dashboard(test, y_test, predictions, mae, mape, model, features):
    """Generate the results dashboard image."""
    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    fig.suptitle("Supply Chain Demand Forecasting — Results", fontsize=14, fontweight="bold")

    axes[0, 0].plot(test["date"], y_test.values, label="Actual", alpha=0.7)
    axes[0, 0].plot(test["date"], predictions, label="Predicted", alpha=0.8, linewidth=2)
    axes[0, 0].set_title(f"Actual vs Predicted (Accuracy: {100-mape:.1f}%)")
    axes[0, 0].legend()

    errors = y_test.values - predictions
    axes[0, 1].hist(errors, bins=30, edgecolor="white", alpha=0.7)
    axes[0, 1].axvline(0, color="red", linestyle="--")
    axes[0, 1].set_title(f"Error Distribution (MAE: {mae:.1f} units)")

    importance = pd.Series(model.feature_importances_, index=features).sort_values()
    importance.tail(8).plot(kind="barh", ax=axes[1, 0])
    axes[1, 0].set_title("Top Demand Drivers")

    axes[1, 1].text(0.5, 0.5, f"Model: Random Forest\nAccuracy: {100-mape:.1f}%\nMAE: {mae:.1f} units\nDays tested: {len(test)}",
                    ha="center", va="center", fontsize=14, transform=axes[1, 1].transAxes)
    axes[1, 1].set_title("Summary")
    axes[1, 1].axis("off")

    plt.tight_layout()
    plt.savefig("forecasting_dashboard.png", dpi=150, bbox_inches="tight")
    print(f"\n✅ Dashboard saved: forecasting_dashboard.png")
    plt.show()


if __name__ == "__main__":
    print("=" * 50)
    print("  SUPPLY CHAIN DEMAND FORECASTING")
    print("=" * 50)

    print("\n1. Generating demand data...")
    df = generate_demand_data()
    print(f"   Created {len(df)} days of data")

    print("\n2. Engineering features...")
    df = add_features(df)
    print(f"   {len(df)} rows with lag/rolling features")

    print("\n3. Training Random Forest model...")
    model, test, y_test, predictions, mae, mape, features = train_model(df)
    print(f"   MAE:      {mae:.1f} units")
    print(f"   MAPE:     {mape:.1f}%")
    print(f"   Accuracy: {100-mape:.1f}%")

    print("\n4. Generating dashboard...")
    create_dashboard(test, y_test, predictions, mae, mape, model, features)

    print("\n" + "=" * 50)
    print("  RESUME LINE:")
    print(f'  "Built a demand forecasting model achieving {100-mape:.0f}%')
    print(f'   accuracy (MAE: {mae:.0f} units) using Random Forest on 1000+ days"')
    print("=" * 50)
