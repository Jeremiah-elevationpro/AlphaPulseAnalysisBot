from __future__ import annotations

import torch
from torch import nn


class SpencerSetupOutcomeModel(nn.Module):
    """Feed-forward multi-head setup outcome model."""

    def __init__(self, input_dim: int):
        super().__init__()
        self.backbone = nn.Sequential(
            nn.Linear(input_dim, 128),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(64, 32),
            nn.ReLU(),
        )
        self.tp1_head = nn.Linear(32, 1)
        self.sl_head = nn.Linear(32, 1)
        self.pips_head = nn.Linear(32, 1)

    def forward(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        z = self.backbone(x)
        return {
            "tp1_logit": self.tp1_head(z).squeeze(-1),
            "sl_logit": self.sl_head(z).squeeze(-1),
            "expected_pips": self.pips_head(z).squeeze(-1),
        }

