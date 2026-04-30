"""Optional 1D-CNN + GRU/LSTM inference for waist-mounted WT901 activity recognition.

The production dashboard must keep working even when PyTorch or a trained model
file is not installed, so this module exposes a safe optional predictor. When a
checkpoint exists it runs neural inference; otherwise the caller can fall back
to the lightweight rule-based classifier.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from app import constants


MODEL_INPUT_CHANNELS = [
    constants.SensorType.ACCELERATION_X,
    constants.SensorType.ACCELERATION_Y,
    constants.SensorType.ACCELERATION_Z,
    constants.SensorType.ANGULAR_VELOCITY_X,
    constants.SensorType.ANGULAR_VELOCITY_Y,
    constants.SensorType.ANGULAR_VELOCITY_Z,
]

DEFAULT_CLASS_NAMES = ["静止", "行走", "跑步", "上楼/下楼", "坐下/起立", "剧烈晃动"]
DEFAULT_SAMPLE_RATE_HZ = 10
DEFAULT_WINDOW_SIZE = 30
DEFAULT_MODEL_FILENAME = "imu_activity_cnn_gru.pt"


def get_default_model_path() -> Path:
    """Return the conventional local path for the trained IMU model."""

    project_root = Path(__file__).resolve().parents[2]
    return project_root / "runtime_data" / "models" / DEFAULT_MODEL_FILENAME


def get_configured_model_path() -> Path:
    """Resolve the model path from env var or the default runtime location."""

    configured_path = os.getenv("SMARTSPACE_IMU_MODEL_PATH")
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return get_default_model_path()


def _unavailable(reason: str, model_path: Path | None = None) -> dict[str, Any]:
    """Build a consistent response when neural inference cannot run."""

    return {
        "available": False,
        "model_type": "1D-CNN-GRU",
        "model_path": str(model_path or get_configured_model_path()),
        "reason": reason,
    }


def _resample_window(samples: list[dict[str, Any]], window_size: int) -> list[list[float]]:
    """Convert irregular timestamped samples into a fixed-size model window."""

    if not samples:
        return []

    ordered = sorted(samples, key=lambda item: item["timestamp"])
    if len(ordered) == window_size:
        selected = ordered
    elif len(ordered) > window_size:
        selected = []
        last_index = len(ordered) - 1
        for index in range(window_size):
            source_index = round(index * last_index / (window_size - 1))
            selected.append(ordered[source_index])
    else:
        selected = ordered[:]
        selected.extend([ordered[-1]] * (window_size - len(ordered)))

    return [[float(sample[channel]) for channel in MODEL_INPUT_CHANNELS] for sample in selected]


def _normalize_window(window: list[list[float]]) -> list[list[float]]:
    """Apply fixed unit scaling while preserving motion intensity.

    Human-activity recognition needs to keep the difference between walking and
    running. Per-window z-score normalization can erase that intensity gap, so
    this lightweight demo uses fixed scaling: acceleration remains in g, while
    angular velocity is scaled from deg/s into a smaller numeric range.
    """

    if not window:
        return []

    normalized = []
    for row in window:
        normalized.append(
            [
                row[0],
                row[1],
                row[2],
                row[3] / 250.0,
                row[4] / 250.0,
                row[5] / 250.0,
            ]
        )
    return normalized


def prepare_model_window(samples: list[dict[str, Any]], window_size: int = DEFAULT_WINDOW_SIZE) -> list[list[float]]:
    """Prepare raw database samples as a fixed `window_size x 6` neural input."""

    return _normalize_window(_resample_window(samples, window_size))


def _build_model_class(torch_module: Any, nn_module: Any):
    """Create the neural network class after PyTorch has been imported."""

    class CnnRecurrentActivityModel(nn_module.Module):
        """Compact HAR model: temporal convolutions followed by GRU/LSTM context."""

        def __init__(
            self,
            input_channels: int = len(MODEL_INPUT_CHANNELS),
            num_classes: int = len(DEFAULT_CLASS_NAMES),
            hidden_size: int = 64,
            recurrent: str = "gru",
            dropout: float = 0.2,
        ) -> None:
            super().__init__()
            self.recurrent_name = recurrent.lower()
            self.feature_extractor = nn_module.Sequential(
                nn_module.Conv1d(input_channels, 64, kernel_size=5, padding=2),
                nn_module.BatchNorm1d(64),
                nn_module.ReLU(),
                nn_module.Conv1d(64, 96, kernel_size=3, padding=1),
                nn_module.BatchNorm1d(96),
                nn_module.ReLU(),
                nn_module.Dropout(dropout),
            )
            recurrent_cls = nn_module.LSTM if self.recurrent_name == "lstm" else nn_module.GRU
            self.recurrent = recurrent_cls(
                input_size=96,
                hidden_size=hidden_size,
                batch_first=True,
                bidirectional=True,
            )
            self.classifier = nn_module.Sequential(
                nn_module.LayerNorm(hidden_size * 2),
                nn_module.Linear(hidden_size * 2, num_classes),
            )

        def forward(self, window):  # noqa: ANN001 - PyTorch tensor type is optional at runtime.
            x = window.transpose(1, 2)
            x = self.feature_extractor(x)
            x = x.transpose(1, 2)
            output, _ = self.recurrent(x)
            return self.classifier(output[:, -1, :])

    return CnnRecurrentActivityModel


@lru_cache(maxsize=1)
def _load_model() -> dict[str, Any]:
    """Load the optional PyTorch checkpoint once per process."""

    model_path = get_configured_model_path()
    if not model_path.exists():
        return _unavailable("未找到训练好的模型文件，已自动使用规则识别兜底", model_path)

    try:
        import torch
        from torch import nn
    except ImportError:
        return _unavailable("当前环境未安装 PyTorch，已自动使用规则识别兜底", model_path)

    try:
        try:
            checkpoint = torch.load(model_path, map_location="cpu", weights_only=True)
        except TypeError:
            checkpoint = torch.load(model_path, map_location="cpu")
        if not isinstance(checkpoint, dict):
            return _unavailable("模型文件格式不正确，需要保存为包含 state_dict 的 checkpoint", model_path)

        class_names = checkpoint.get("class_names", DEFAULT_CLASS_NAMES)
        model_config = checkpoint.get("model_config", {})
        recurrent = model_config.get("recurrent", "gru")
        hidden_size = int(model_config.get("hidden_size", 64))
        dropout = float(model_config.get("dropout", 0.2))
        model_class = _build_model_class(torch, nn)
        model = model_class(
            num_classes=len(class_names),
            hidden_size=hidden_size,
            recurrent=recurrent,
            dropout=dropout,
        )
        state_dict = checkpoint.get("state_dict")
        if not state_dict:
            return _unavailable("模型文件缺少 state_dict，已自动使用规则识别兜底", model_path)
        model.load_state_dict(state_dict)
        model.eval()
        return {
            "available": True,
            "torch": torch,
            "model": model,
            "model_path": str(model_path),
            "model_type": f"1D-CNN-{recurrent.upper()}",
            "class_names": class_names,
            "window_size": int(model_config.get("window_size", DEFAULT_WINDOW_SIZE)),
            "sample_rate_hz": int(model_config.get("sample_rate_hz", DEFAULT_SAMPLE_RATE_HZ)),
        }
    except Exception as exc:  # pragma: no cover - defensive path for local model issues.
        return _unavailable(f"模型加载失败：{exc}", model_path)


def predict_activity(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Run optional neural inference on IMU samples."""

    loaded = _load_model()
    if not loaded.get("available"):
        return loaded

    window_size = loaded["window_size"]
    if len(samples) < 6:
        return _unavailable("IMU 样本过少，深度模型暂不推理", Path(loaded["model_path"]))

    window = prepare_model_window(samples, window_size=window_size)
    if not window:
        return _unavailable("IMU 窗口为空，深度模型暂不推理", Path(loaded["model_path"]))

    torch = loaded["torch"]
    tensor = torch.tensor([window], dtype=torch.float32)
    with torch.no_grad():
        logits = loaded["model"](tensor)
        probabilities = torch.softmax(logits, dim=1)[0].tolist()
    best_index = max(range(len(probabilities)), key=probabilities.__getitem__)
    class_names = loaded["class_names"]

    return {
        "available": True,
        "model_type": loaded["model_type"],
        "model_path": loaded["model_path"],
        "activity": class_names[best_index],
        "confidence": round(float(probabilities[best_index]), 4),
        "probabilities": {
            class_name: round(float(probability), 4)
            for class_name, probability in zip(class_names, probabilities)
        },
        "window_size": window_size,
        "sample_rate_hz": loaded["sample_rate_hz"],
    }
