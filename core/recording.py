from __future__ import annotations
import os
from dataclasses import dataclass, field, asdict
from typing import Optional
import yaml


@dataclass
class HistoryEntry:
    """Records a system-made change to an ElementFingerprint.

    Used for revertable heal promotions (§3.1 of the design). Manual edits
    do not record history — they overwrite. `source` distinguishes heal-
    driven changes from auto-insertions and (future) manual entries.
    """
    timestamp: str
    run_id: str
    source: str                              # "heal" | "auto_insert" | "manual_edit"
    previous_primary_locator: dict
    previous_fallback_locators: list[dict]
    previous_attributes: dict
    confidence: Optional[float] = None       # heal confidence, None for non-heal sources

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "HistoryEntry":
        return cls(
            timestamp=d.get("timestamp", ""),
            run_id=d.get("run_id", ""),
            source=d.get("source", ""),
            previous_primary_locator=dict(d.get("previous_primary_locator", {})),
            previous_fallback_locators=[dict(x) for x in d.get("previous_fallback_locators", [])],
            previous_attributes=dict(d.get("previous_attributes", {})),
            confidence=d.get("confidence"),
        )


@dataclass
class ElementFingerprint:
    """A multi-attribute fingerprint for one recorded element.

    The `id` is stable across every Step in the Recording that touches this
    physical element (the capture engine dedups by xpath + neighborhood
    signature). `primary_locator` is the strategy the replayer tries first;
    `fallback_locators` are tried in order on failure before invoking the
    healer.
    """
    id: str
    primary_locator: dict
    fallback_locators: list[dict]
    attributes: dict
    page_context: dict
    fingerprint_history: list[HistoryEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        # asdict recurses into HistoryEntry list — already dict form
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ElementFingerprint":
        return cls(
            id=d["id"],
            primary_locator=dict(d["primary_locator"]),
            fallback_locators=[dict(x) for x in d.get("fallback_locators", [])],
            attributes=dict(d.get("attributes", {})),
            page_context=dict(d.get("page_context", {})),
            fingerprint_history=[
                HistoryEntry.from_dict(h) for h in d.get("fingerprint_history", [])
            ],
        )


@dataclass
class NetworkCapture:
    url: str
    method: str
    status: int
    request_body: str = ""
    response_body: str = ""
    response_headers: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> NetworkCapture:
        return cls(**{**d, "response_headers": dict(d.get("response_headers", {}))})


@dataclass
class Step:
    index: int
    action: str  # "fill" | "click" | "hover" | "select" | "check" | "uncheck" | "press" | "navigate" | "wait"
    element: Optional[ElementFingerprint] = None
    value: Optional[str] = None
    timestamp_ms: int = 0
    revealed_elements: list[str] = field(default_factory=list)
    hidden_elements: list[str] = field(default_factory=list)
    network: list[NetworkCapture] = field(default_factory=list)
    error_elements: list[ElementFingerprint] = field(default_factory=list)
    inserted_by: Optional[str] = None  # "auto-heal" | "user_edit" | None (captured)
    # When True, replay pauses at this step and shows an in-page banner with
    # a Resume button. Used for fields that can't be replayed automatically
    # (captcha, OTP, security questions). Auto-detected at record time from
    # field name/placeholder/label; user can override in the recording editor.
    # A True value on any step forces the replay browser to launch headed.
    needs_manual: bool = False
    # When True, the AI keeps this step's recorded value verbatim in every
    # generated test case and never generates a value for it (e.g. username,
    # password, a fixed account number). User-toggled in the recording editor.
    locked_value: bool = False
    # Optional free-text hint sent to the AI for THIS field only — a targeted
    # fix when the model repeatedly produces wrong values for it.
    field_context: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "action": self.action,
            "element": self.element.to_dict() if self.element else None,
            "value": self.value,
            "timestamp_ms": self.timestamp_ms,
            "revealed_elements": list(self.revealed_elements),
            "hidden_elements": list(self.hidden_elements),
            "network": [n.to_dict() for n in self.network],
            "error_elements": [e.to_dict() for e in self.error_elements],
            "inserted_by": self.inserted_by,
            "needs_manual": self.needs_manual,
            "locked_value": self.locked_value,
            "field_context": self.field_context,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Step:
        return cls(
            index=d["index"],
            action=d["action"],
            element=ElementFingerprint.from_dict(d["element"]) if d.get("element") else None,
            value=d.get("value"),
            timestamp_ms=d.get("timestamp_ms", 0),
            revealed_elements=list(d.get("revealed_elements", [])),
            hidden_elements=list(d.get("hidden_elements", [])),
            network=[NetworkCapture.from_dict(n) for n in d.get("network", [])],
            error_elements=[ElementFingerprint.from_dict(e) for e in d.get("error_elements", [])],
            inserted_by=d.get("inserted_by"),
            needs_manual=bool(d.get("needs_manual", False)),
            locked_value=bool(d.get("locked_value", False)),
            field_context=d.get("field_context"),
        )


@dataclass
class SuccessSignal:
    url_pattern: str
    required_elements: list[ElementFingerprint] = field(default_factory=list)
    forbidden_elements: list[ElementFingerprint] = field(default_factory=list)
    captured_at: str = ""

    def to_dict(self) -> dict:
        return {
            "url_pattern": self.url_pattern,
            "required_elements": [e.to_dict() for e in self.required_elements],
            "forbidden_elements": [e.to_dict() for e in self.forbidden_elements],
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SuccessSignal:
        return cls(
            url_pattern=d["url_pattern"],
            required_elements=[ElementFingerprint.from_dict(e) for e in d.get("required_elements", [])],
            forbidden_elements=[ElementFingerprint.from_dict(e) for e in d.get("forbidden_elements", [])],
            captured_at=d.get("captured_at", ""),
        )


@dataclass
class Recording:
    id: str
    name: str
    kind: str  # "login" | "scenario"
    application_id: str
    created_at: str
    start_url: str
    steps: list[Step] = field(default_factory=list)
    success_signal: Optional[SuccessSignal] = None
    healed_at: Optional[str] = None
    acknowledged_missing_required: list[str] = field(default_factory=list)
    # Snapshot of the form schema at recording-save time. Each entry is a slim
    # fingerprint: id, name, nearest_label_text, autocomplete, tag, is_required.
    # Replay-time schema diff against this list surfaces fields added after the
    # recording was made (regardless of whether they're marked required).
    # Older recordings load with this empty — schema diff is skipped for them
    # and only the existing required-field detection fires.
    record_time_fields: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "application_id": self.application_id,
            "created_at": self.created_at,
            "start_url": self.start_url,
            "steps": [s.to_dict() for s in self.steps],
            "success_signal": self.success_signal.to_dict() if self.success_signal else None,
            "healed_at": self.healed_at,
            "acknowledged_missing_required": list(self.acknowledged_missing_required),
            "record_time_fields": [dict(f) for f in self.record_time_fields],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Recording:
        return cls(
            id=d["id"],
            name=d["name"],
            kind=d["kind"],
            application_id=d["application_id"],
            created_at=d.get("created_at", ""),
            start_url=d["start_url"],
            steps=[Step.from_dict(s) for s in d.get("steps", [])],
            success_signal=SuccessSignal.from_dict(d["success_signal"]) if d.get("success_signal") else None,
            healed_at=d.get("healed_at"),
            acknowledged_missing_required=list(d.get("acknowledged_missing_required", [])),
            record_time_fields=[dict(f) for f in d.get("record_time_fields", [])],
        )


def save_recording(path: str, rec: Recording) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(rec.to_dict(), f, sort_keys=False)


def load_recording(path: str) -> Recording:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Recording.from_dict(data)
