from __future__ import annotations
import os
from dataclasses import dataclass, field, asdict
from typing import Optional
import yaml


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

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ElementFingerprint:
        return cls(
            id=d["id"],
            primary_locator=dict(d["primary_locator"]),
            fallback_locators=[dict(x) for x in d.get("fallback_locators", [])],
            attributes=dict(d.get("attributes", {})),
            page_context=dict(d.get("page_context", {})),
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
    action: str  # "fill" | "click" | "select" | "check" | "uncheck" | "press" | "navigate" | "wait"
    element: Optional[ElementFingerprint] = None
    value: Optional[str] = None
    timestamp_ms: int = 0
    revealed_elements: list[str] = field(default_factory=list)
    hidden_elements: list[str] = field(default_factory=list)
    network: list[NetworkCapture] = field(default_factory=list)
    error_elements: list[ElementFingerprint] = field(default_factory=list)

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
        )


def save_recording(path: str, rec: Recording) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(rec.to_dict(), f, sort_keys=False)


def load_recording(path: str) -> Recording:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Recording.from_dict(data)
