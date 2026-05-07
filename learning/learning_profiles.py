from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class LearningProfile:
    strategy_type: str
    sample_size: int
    win_rate: float
    tp1_rate: float
    tp2_rate: float
    tp3_rate: float
    avg_pips: float
    net_pips: float
    confidence_tier: str
    recommended_action: str
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_analyst_learning_profile(rows: list[dict[str, Any]], profile_used: str) -> dict[str, Any]:
    sample_size = len(rows)
    wins = sum(1 for row in rows if str(row.get("result") or "").upper() in {"WIN", "STRONG_WIN", "BREAKEVEN_WIN"})
    losses = sum(1 for row in rows if str(row.get("result") or "").upper() == "LOSS")
    net_pips = round(sum(float(row.get("pips_result") or 0.0) for row in rows), 2)
    tp1_rate = round((sum(1 for row in rows if row.get("tp1_hit")) / sample_size) * 100.0, 2) if sample_size else 0.0
    tp2_rate = round((sum(1 for row in rows if row.get("tp2_hit")) / sample_size) * 100.0, 2) if sample_size else 0.0
    tp3_rate = round((sum(1 for row in rows if row.get("tp3_hit")) / sample_size) * 100.0, 2) if sample_size else 0.0
    if sample_size >= 50:
        confidence_tier = "high"
    elif sample_size >= 25:
        confidence_tier = "medium"
    elif sample_size >= 10:
        confidence_tier = "low"
    else:
        confidence_tier = "insufficient_sample"
    win_rate = round((wins / sample_size) * 100.0, 2) if sample_size else 0.0
    recommended_weight = 1.15 if win_rate >= 65.0 and sample_size >= 10 else 0.75 if win_rate < 35.0 and sample_size >= 10 else 1.0
    return {
        "profile_used": profile_used,
        "sample_size": sample_size,
        "win_rate": win_rate,
        "tp1_rate": tp1_rate,
        "tp2_rate": tp2_rate,
        "tp3_rate": tp3_rate,
        "loss_rate": round((losses / sample_size) * 100.0, 2) if sample_size else 0.0,
        "avg_pips": round(net_pips / sample_size, 2) if sample_size else 0.0,
        "net_pips": net_pips,
        "confidence_tier": confidence_tier,
        "recommended_weight": recommended_weight,
        "recommended_action": "boost" if recommended_weight > 1.0 else "block" if recommended_weight < 1.0 else "allow",
    }
