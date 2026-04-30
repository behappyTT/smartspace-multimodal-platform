"""Train a waist-mounted WT901 1D-CNN + GRU/LSTM activity model.

Expected CSV columns:
timestamp,label,acceleration_x,acceleration_y,acceleration_z,
angular_velocity_x,angular_velocity_y,angular_velocity_z

Example:
python scripts/train_imu_cnn_gru.py --csv runtime_data/labels/waist_imu.csv
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services import imu_deep_model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train WT901 waist IMU activity model.")
    parser.add_argument("--csv", required=True, help="Labeled IMU CSV path.")
    parser.add_argument("--output", default=str(imu_deep_model.get_default_model_path()), help="Output .pt checkpoint path.")
    parser.add_argument("--window-size", type=int, default=imu_deep_model.DEFAULT_WINDOW_SIZE, help="Samples per window.")
    parser.add_argument("--stride", type=int, default=10, help="Sliding window stride.")
    parser.add_argument("--epochs", type=int, default=30, help="Training epochs.")
    parser.add_argument("--batch-size", type=int, default=32, help="Mini-batch size.")
    parser.add_argument("--lr", type=float, default=1e-3, help="Learning rate.")
    parser.add_argument("--recurrent", choices=["gru", "lstm"], default="gru", help="Recurrent layer type.")
    return parser.parse_args()


def load_rows(csv_path: Path) -> list[dict]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        rows = []
        for row in reader:
            rows.append(
                {
                    "timestamp": row["timestamp"],
                    "label": row["label"],
                    **{
                        channel: float(row[channel])
                        for channel in imu_deep_model.MODEL_INPUT_CHANNELS
                    },
                }
            )
    return rows


def build_windows(rows: list[dict], window_size: int, stride: int) -> tuple[list[list[list[float]]], list[str]]:
    windows: list[list[list[float]]] = []
    labels: list[str] = []

    # 按连续标签片段分别滑窗，避免“行走->跑步”边界窗口混入两类动作。
    segments: list[list[dict]] = []
    current_segment: list[dict] = []
    for row in rows:
        if current_segment and row["label"] != current_segment[-1]["label"]:
            segments.append(current_segment)
            current_segment = []
        current_segment.append(row)
    if current_segment:
        segments.append(current_segment)

    for segment in segments:
        label = segment[0]["label"]
        for start_index in range(0, max(0, len(segment) - window_size + 1), stride):
            window_rows = segment[start_index : start_index + window_size]
            windows.append(imu_deep_model.prepare_model_window(window_rows, window_size))
            labels.append(label)
    return windows, labels


def main() -> None:
    args = parse_args()
    csv_path = Path(args.csv)
    output_path = Path(args.output)
    rows = load_rows(csv_path)
    windows, labels = build_windows(rows, args.window_size, args.stride)
    if len(windows) < 10:
        raise RuntimeError("可用训练窗口少于 10 个，请采集更多带标签的腰部 WT901 数据。")

    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, TensorDataset, random_split
    except ImportError as exc:
        raise RuntimeError("训练深度模型需要先安装 PyTorch，例如 pip install torch。") from exc

    random.seed(42)
    torch.manual_seed(42)
    class_names = sorted(set(labels))
    label_to_index = {label: index for index, label in enumerate(class_names)}
    x_tensor = torch.tensor(windows, dtype=torch.float32)
    y_tensor = torch.tensor([label_to_index[label] for label in labels], dtype=torch.long)
    dataset = TensorDataset(x_tensor, y_tensor)
    train_size = max(1, int(len(dataset) * 0.8))
    val_size = len(dataset) - train_size
    train_dataset, val_dataset = random_split(dataset, [train_size, val_size])
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=args.batch_size)

    model_class = imu_deep_model._build_model_class(torch, nn)
    model = model_class(num_classes=len(class_names), recurrent=args.recurrent)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    criterion = nn.CrossEntropyLoss()

    for epoch in range(1, args.epochs + 1):
        model.train()
        total_loss = 0.0
        for batch_x, batch_y in train_loader:
            optimizer.zero_grad()
            loss = criterion(model(batch_x), batch_y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item() * len(batch_x)

        model.eval()
        correct = 0
        total = 0
        with torch.no_grad():
            for batch_x, batch_y in val_loader:
                prediction = model(batch_x).argmax(dim=1)
                correct += int((prediction == batch_y).sum().item())
                total += len(batch_y)
        val_accuracy = correct / total if total else 0.0
        train_loss = total_loss / len(train_dataset)
        print(f"epoch={epoch:03d} loss={train_loss:.4f} val_acc={val_accuracy:.3f}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": model.state_dict(),
            "class_names": class_names,
            "model_config": {
                "recurrent": args.recurrent,
                "hidden_size": 64,
                "dropout": 0.2,
                "window_size": args.window_size,
                "sample_rate_hz": imu_deep_model.DEFAULT_SAMPLE_RATE_HZ,
            },
        },
        output_path,
    )
    print(f"saved_model={output_path}")
    print(f"class_names={class_names}")


if __name__ == "__main__":
    main()
