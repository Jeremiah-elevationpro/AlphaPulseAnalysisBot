from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Optional


class AlertDedupeManager:
    def __init__(self, ttl_hours: Optional[dict[str, int]] = None):
        self.sent: dict[str, dict[str, dict]] = {}
        self.skipped: dict[str, int] = {}
        self.last_skip_reason: str = ""
        self._ttl_hours = ttl_hours or {}

    def should_send_alert(
        self,
        alert_type: str,
        event_key: str,
        payload_signature: str,
        cooldown_seconds: int | None = None,
    ) -> tuple[bool, str]:
        now = datetime.now(timezone.utc)
        entry = (self.sent.get(alert_type) or {}).get(event_key)
        if not entry:
            return True, "new_event"

        sent_at = self._parse_dt(entry.get("sent_at"))
        if entry.get("signature") == payload_signature:
            if cooldown_seconds and sent_at and (now - sent_at) < timedelta(seconds=cooldown_seconds):
                self._bump_skip(alert_type, "cooldown_active")
                return False, "cooldown_active"
            self._bump_skip(alert_type, "unchanged_signature")
            return False, "unchanged_signature"

        if cooldown_seconds and sent_at and (now - sent_at) < timedelta(seconds=cooldown_seconds):
            self._bump_skip(alert_type, "cooldown_active")
            return False, "cooldown_active"
        return True, "new_event"

    def mark_alert_sent(
        self,
        alert_type: str,
        event_key: str,
        payload_signature: str,
        *,
        metadata: Optional[dict] = None,
    ) -> None:
        self.sent.setdefault(alert_type, {})[event_key] = {
            "signature": payload_signature,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }

    def find_recent_matching(
        self,
        alert_type: str,
        matcher: Callable[[str, dict], bool],
        cooldown_seconds: int | None = None,
    ) -> tuple[bool, Optional[str]]:
        now = datetime.now(timezone.utc)
        for event_key, entry in (self.sent.get(alert_type) or {}).items():
            sent_at = self._parse_dt(entry.get("sent_at"))
            if cooldown_seconds and sent_at and (now - sent_at) >= timedelta(seconds=cooldown_seconds):
                continue
            if matcher(event_key, entry):
                return True, event_key
        return False, None

    def cleanup(self) -> int:
        now = datetime.now(timezone.utc)
        removed = 0
        for alert_type, entries in list(self.sent.items()):
            cutoff = now - timedelta(hours=self._ttl_hours.get(alert_type, 48))
            filtered = {}
            for key, entry in entries.items():
                sent_at = self._parse_dt(entry.get("sent_at"))
                if sent_at and sent_at >= cutoff:
                    filtered[key] = entry
                else:
                    removed += 1
            self.sent[alert_type] = filtered
        return removed

    def to_payload(self) -> dict:
        return {
            "sent": self.sent,
            "skipped": self.skipped,
            "last_skip_reason": self.last_skip_reason,
        }

    def load_payload(self, payload: Optional[dict]) -> None:
        data = payload or {}
        self.sent = {
            str(alert_type): {str(key): dict(value) for key, value in (entries or {}).items()}
            for alert_type, entries in (data.get("sent") or {}).items()
        }
        self.skipped = {str(key): int(value) for key, value in (data.get("skipped") or {}).items()}
        self.last_skip_reason = str(data.get("last_skip_reason") or "")

    def counts(self) -> dict[str, int]:
        return {alert_type: len(entries) for alert_type, entries in self.sent.items()}

    @staticmethod
    def _parse_dt(raw: object) -> Optional[datetime]:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            return None

    def _bump_skip(self, alert_type: str, reason: str) -> None:
        key = f"{alert_type}_{reason}"
        self.skipped[key] = int(self.skipped.get(key, 0) or 0) + 1
        self.last_skip_reason = reason
