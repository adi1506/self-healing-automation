# Scenario Recording Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add browser-based scenario recording as a primary scenario input. User drives a headed Chromium through a target app's happy path; the recorder captures multi-attribute element fingerprints + steps; replay walks those steps to reproduce the outcome. Auth via human-in-the-loop `storageState` refresh. This plan covers spec §13 phases 1–4 — the demoable milestone.

**Architecture:** Headed Playwright Chromium launched by the Streamlit app on the VM. An injected JavaScript content script (`page.add_init_script`) listens for user interactions and builds rich fingerprints for each touched element. Captured timeline persists as a `Recording` (YAML) attached to a `Scenario`. Replay loads `storageState` for auth, then walks the recorded steps using the fingerprints to find elements.

**Tech Stack:** Python 3.11+ async Playwright (1.58), `cryptography.fernet` for storageState encryption, PyYAML for persistence, Streamlit for UI, pytest + `pytest-asyncio` for tests.

**Out of scope for this plan** (deferred to follow-on plans):
- Healing fingerprints (existing locator-chain fallback is used; spec §10 healer rewrite comes later).
- AI test case generation from recordings (existing scanner-based AI flow untouched; spec §8 comes later).
- DOM diff capture (`revealed_elements` / `hidden_elements` fields exist in the model but stay empty in this plan; spec §6 MutationObserver work comes later).
- Server response capture for failure grounding (spec §7.3 comes later).
- Multiple recordings per scenario (data model supports it via `recordings: list[Recording]`, but UI only adds one).

---

## File Structure

**New files:**

| Path | Responsibility |
|---|---|
| `core/recording.py` | `Recording`, `Step`, `ElementFingerprint`, `NetworkCapture`, `SuccessSignal` dataclasses + YAML helpers |
| `core/applications.py` | `Application` dataclass + YAML CRUD |
| `core/auth_session.py` | Fernet key resolution + encrypted storageState file management |
| `core/capture/__init__.py` | Package marker |
| `core/capture/inject.js` | Capture content script injected into the recorder Chromium |
| `core/recorder.py` | `RecorderSession` class — orchestrates Playwright, injection, event collection |
| `core/replay.py` | `replay_recording(recording, data_overrides, ...)` — walks a Recording's steps and reports the outcome |
| `pages/6_recordings.py` | Streamlit page: applications + login recordings |
| `ui/recording/__init__.py` | Package marker |
| `ui/recording/session_panel.py` | Streamlit "recording in progress" widget + stop button |
| `ui/recording/success_signal_picker.py` | Streamlit picker for confirming login success elements |
| `tests/test_recording_model.py` | `Recording` + sub-types serialization tests |
| `tests/test_applications.py` | `Application` CRUD tests |
| `tests/test_auth_session.py` | Encryption + storageState tests |
| `tests/test_capture_inject.py` | Playwright-driven tests for the injected JS |
| `tests/test_recorder.py` | `RecorderSession` end-to-end against `test_form/sample_form.html` |
| `tests/test_replay.py` | Replay engine tests against a recorded fixture |

**Modified files:**

| Path | Change |
|---|---|
| `core/scenarios.py` | Add `"recorded"` to `VALID_KINDS`, add `application_id`, `recordings`, `ai_test_cases` fields, extend validation |
| `tests/test_scenarios.py` (or wherever scenarios are tested today) | Add tests for the `"recorded"` kind validation |

**Data directories (gitignored, created at runtime):**

- `data/applications/` — `<id>.yaml`
- `data/applications/<id>/login_recording.yaml`
- `data/storage_states/<application_id>.enc` — Fernet-encrypted storageState JSON

---

## Task 1: Recording data model — `ElementFingerprint`

The smallest piece — a fingerprint dataclass for one captured element. Every other type composes this.

**Files:**
- Create: `core/recording.py`
- Create: `tests/test_recording_model.py`

- [ ] **Step 1.1: Write the failing test**

In `tests/test_recording_model.py`:
```python
from core.recording import ElementFingerprint


def test_element_fingerprint_round_trip_dict():
    fp = ElementFingerprint(
        id="el-001",
        primary_locator={"strategy": "id", "value": "pan"},
        fallback_locators=[
            {"strategy": "name", "value": "pan"},
            {"strategy": "css", "value": "input[name='pan']"},
        ],
        attributes={
            "tag": "input",
            "type": "text",
            "id": "pan",
            "nearest_label_text": "PAN",
            "html5_constraints": {"required": True, "maxlength": 10},
        },
        page_context={"url": "https://example.com/kyc", "section_label": "KYC"},
    )
    d = fp.to_dict()
    fp2 = ElementFingerprint.from_dict(d)
    assert fp2 == fp


def test_element_fingerprint_minimum_required_fields():
    fp = ElementFingerprint(
        id="el-002",
        primary_locator={"strategy": "css", "value": "button.submit"},
        fallback_locators=[],
        attributes={"tag": "button"},
        page_context={"url": "https://example.com"},
    )
    assert fp.id == "el-002"
    assert fp.fallback_locators == []
```

- [ ] **Step 1.2: Run the test to verify it fails**

Run: `pytest tests/test_recording_model.py -v`
Expected: `ModuleNotFoundError: No module named 'core.recording'`

- [ ] **Step 1.3: Implement `ElementFingerprint`**

In `core/recording.py`:
```python
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any


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
```

- [ ] **Step 1.4: Run the test to verify it passes**

Run: `pytest tests/test_recording_model.py -v`
Expected: both tests PASS.

- [ ] **Step 1.5: Commit**

```bash
git add -f core/recording.py tests/test_recording_model.py
git commit -m "feat(recording): ElementFingerprint dataclass"
```

---

## Task 2: Recording data model — `Step`, `NetworkCapture`, `SuccessSignal`, `Recording`

Compose the remaining types and add YAML round-trip.

**Files:**
- Modify: `core/recording.py`
- Modify: `tests/test_recording_model.py`

- [ ] **Step 2.1: Write the failing tests**

Append to `tests/test_recording_model.py`:
```python
import os
import tempfile
from core.recording import (
    Step, NetworkCapture, SuccessSignal, Recording,
    save_recording, load_recording,
)


def _fp(id_: str, name: str) -> ElementFingerprint:
    return ElementFingerprint(
        id=id_,
        primary_locator={"strategy": "name", "value": name},
        fallback_locators=[],
        attributes={"tag": "input", "name": name},
        page_context={"url": "https://example.com"},
    )


def test_step_with_action_fill():
    s = Step(
        index=0,
        action="fill",
        element=_fp("el-001", "pan"),
        value="ABCDE1234F",
        timestamp_ms=1500,
    )
    assert s.action == "fill"
    assert s.value == "ABCDE1234F"
    assert s.revealed_elements == []
    assert s.network == []


def test_step_with_action_navigate_has_no_element():
    s = Step(index=0, action="navigate", element=None, value="https://example.com/next")
    assert s.element is None


def test_recording_yaml_round_trip(tmp_path):
    rec = Recording(
        id="rec-001",
        name="Happy path: KYC",
        kind="scenario",
        application_id="app-001",
        created_at="2026-05-15T10:30:00",
        start_url="https://example.com/kyc",
        steps=[
            Step(index=0, action="fill", element=_fp("el-001", "pan"), value="ABCDE1234F"),
            Step(index=1, action="click", element=_fp("el-002", "submit"), value=None),
        ],
        success_signal=None,
    )
    path = tmp_path / "rec-001.yaml"
    save_recording(str(path), rec)
    loaded = load_recording(str(path))
    assert loaded == rec


def test_login_recording_with_success_signal():
    sig = SuccessSignal(
        url_pattern="/dashboard",
        required_elements=[_fp("el-100", "user-menu")],
        forbidden_elements=[_fp("el-101", "username")],
        captured_at="2026-05-15T10:31:00",
    )
    rec = Recording(
        id="login-001",
        name="FinnOne login",
        kind="login",
        application_id="app-001",
        created_at="2026-05-15T10:30:00",
        start_url="https://10.0.42.28:7256/login",
        steps=[],
        success_signal=sig,
    )
    assert rec.kind == "login"
    assert rec.success_signal.url_pattern == "/dashboard"
```

- [ ] **Step 2.2: Run the tests to verify they fail**

Run: `pytest tests/test_recording_model.py -v`
Expected: ImportError on the new names.

- [ ] **Step 2.3: Implement the remaining types**

Append to `core/recording.py`:
```python
import os
import yaml
from typing import Literal, Optional


@dataclass
class NetworkCapture:
    url: str
    method: str
    status: int
    request_body: str = ""        # truncated to 4 KB upstream
    response_body: str = ""       # truncated to 4 KB upstream
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
```

- [ ] **Step 2.4: Run the tests to verify they pass**

Run: `pytest tests/test_recording_model.py -v`
Expected: all tests PASS.

- [ ] **Step 2.5: Commit**

```bash
git add core/recording.py tests/test_recording_model.py
git commit -m "feat(recording): Step, NetworkCapture, SuccessSignal, Recording with YAML I/O"
```

---

## Task 3: Application model + CRUD

**Files:**
- Create: `core/applications.py`
- Create: `tests/test_applications.py`

- [ ] **Step 3.1: Write the failing tests**

In `tests/test_applications.py`:
```python
import pytest
from core.applications import (
    Application, save_application, load_application,
    list_applications, delete_application,
)


def test_application_minimal_round_trip(tmp_path):
    app = Application(
        id="app-finnone",
        name="FinnOne Neo",
        base_url_pattern="10.0.42.28:7256",
    )
    save_application(str(tmp_path), app)
    loaded = load_application(str(tmp_path), "app-finnone")
    assert loaded == app


def test_application_with_login_recording_pointer(tmp_path):
    app = Application(
        id="app-hdb",
        name="HDB Financial",
        base_url_pattern="mcoput.hdbfs.com",
        login_recording_id="login-001",
        storage_state_path="data/storage_states/app-hdb.enc",
        storage_state_captured_at="2026-05-15T10:00:00",
        storage_state_expires_at="2026-05-16T10:00:00",
    )
    save_application(str(tmp_path), app)
    loaded = load_application(str(tmp_path), "app-hdb")
    assert loaded == app


def test_list_and_delete(tmp_path):
    save_application(str(tmp_path), Application(id="a", name="A", base_url_pattern="a.com"))
    save_application(str(tmp_path), Application(id="b", name="B", base_url_pattern="b.com"))
    ids = [a.id for a in list_applications(str(tmp_path))]
    assert sorted(ids) == ["a", "b"]
    delete_application(str(tmp_path), "a")
    ids = [a.id for a in list_applications(str(tmp_path))]
    assert ids == ["b"]


def test_list_empty_dir_returns_empty():
    assert list_applications("/nonexistent/path") == []
```

- [ ] **Step 3.2: Run the tests to verify they fail**

Run: `pytest tests/test_applications.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3.3: Implement `core/applications.py`**

```python
from __future__ import annotations
import os
from dataclasses import dataclass, field, asdict
from typing import Optional
import yaml


@dataclass
class Application:
    id: str
    name: str
    base_url_pattern: str
    login_recording_id: Optional[str] = None
    storage_state_path: Optional[str] = None
    storage_state_captured_at: Optional[str] = None
    storage_state_expires_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _path(data_dir: str, app_id: str) -> str:
    return os.path.join(data_dir, f"{app_id}.yaml")


def save_application(data_dir: str, app: Application) -> None:
    os.makedirs(data_dir, exist_ok=True)
    with open(_path(data_dir, app.id), "w", encoding="utf-8") as f:
        yaml.safe_dump(app.to_dict(), f, sort_keys=False)


def load_application(data_dir: str, app_id: str) -> Application:
    with open(_path(data_dir, app_id), encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Application(**data)


def list_applications(data_dir: str) -> list[Application]:
    if not os.path.isdir(data_dir):
        return []
    out: list[Application] = []
    for fname in sorted(os.listdir(data_dir)):
        if not fname.endswith(".yaml") or fname.startswith("_"):
            continue
        try:
            out.append(load_application(data_dir, fname[:-5]))
        except Exception:
            continue
    return out


def delete_application(data_dir: str, app_id: str) -> None:
    p = _path(data_dir, app_id)
    if os.path.exists(p):
        os.remove(p)
```

- [ ] **Step 3.4: Run the tests to verify they pass**

Run: `pytest tests/test_applications.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 3.5: Commit**

```bash
git add core/applications.py tests/test_applications.py
git commit -m "feat(applications): Application dataclass + YAML CRUD"
```

---

## Task 4: Auth session — encryption helpers

`storageState` is sensitive (it contains session cookies). Encrypt at rest with Fernet using a key from `data/settings.yaml`.

**Files:**
- Create: `core/auth_session.py`
- Create: `tests/test_auth_session.py`

- [ ] **Step 4.1: Write the failing test**

In `tests/test_auth_session.py`:
```python
import json
import pytest
from pathlib import Path
from core.auth_session import (
    resolve_fernet_key, encrypt_storage_state, decrypt_storage_state,
    MissingStorageStateKey,
)


def test_resolve_key_from_settings_file(tmp_path):
    settings_path = tmp_path / "settings.yaml"
    # Fernet key is a urlsafe-base64-encoded 32-byte value
    settings_path.write_text("storage_state_key: 'YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU='\n", encoding="utf-8")
    key = resolve_fernet_key(str(settings_path))
    assert isinstance(key, bytes)
    assert len(key) == 44  # urlsafe-b64 of 32 bytes


def test_resolve_key_missing_raises(tmp_path):
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("ollama_host: 'http://localhost'\n", encoding="utf-8")
    with pytest.raises(MissingStorageStateKey):
        resolve_fernet_key(str(settings_path))


def test_encrypt_decrypt_round_trip(tmp_path):
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("storage_state_key: 'YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU='\n", encoding="utf-8")
    payload = {"cookies": [{"name": "session", "value": "abc"}], "origins": []}
    blob = encrypt_storage_state(payload, settings_path=str(settings_path))
    assert blob != json.dumps(payload).encode()  # actually encrypted
    out = decrypt_storage_state(blob, settings_path=str(settings_path))
    assert out == payload
```

- [ ] **Step 4.2: Run the test to verify it fails**

Run: `pytest tests/test_auth_session.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 4.3: Implement encryption helpers**

In `core/auth_session.py`:
```python
from __future__ import annotations
import json
import os
from pathlib import Path
import yaml
from cryptography.fernet import Fernet

DEFAULT_SETTINGS_PATH = "data/settings.yaml"


class MissingStorageStateKey(RuntimeError):
    """Raised when settings.yaml has no storage_state_key entry.

    The user must generate one and set it. Generate with:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    """


def resolve_fernet_key(settings_path: str = DEFAULT_SETTINGS_PATH) -> bytes:
    p = Path(settings_path)
    if not p.exists():
        raise MissingStorageStateKey(
            f"settings.yaml not found at {settings_path}; cannot resolve storage_state_key"
        )
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    raw = data.get("storage_state_key")
    if not raw:
        raise MissingStorageStateKey(
            "settings.yaml is missing storage_state_key. Generate one with:\n"
            "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
            "and add it to data/settings.yaml as 'storage_state_key: <value>'."
        )
    return raw.encode() if isinstance(raw, str) else raw


def encrypt_storage_state(payload: dict, *, settings_path: str = DEFAULT_SETTINGS_PATH) -> bytes:
    key = resolve_fernet_key(settings_path)
    return Fernet(key).encrypt(json.dumps(payload).encode("utf-8"))


def decrypt_storage_state(blob: bytes, *, settings_path: str = DEFAULT_SETTINGS_PATH) -> dict:
    key = resolve_fernet_key(settings_path)
    return json.loads(Fernet(key).decrypt(blob).decode("utf-8"))
```

- [ ] **Step 4.4: Run the test to verify it passes**

Run: `pytest tests/test_auth_session.py -v`
Expected: 3 tests PASS.

- [ ] **Step 4.5: Commit**

```bash
git add core/auth_session.py tests/test_auth_session.py
git commit -m "feat(auth): Fernet encryption helpers for storageState"
```

---

## Task 5: Auth session — storageState file management + expiry check

Build CRUD + expiry-checking around the encryption helpers.

**Files:**
- Modify: `core/auth_session.py`
- Modify: `tests/test_auth_session.py`

- [ ] **Step 5.1: Write the failing tests**

Append to `tests/test_auth_session.py`:
```python
from datetime import datetime, timezone, timedelta
from core.applications import Application
from core.auth_session import (
    save_storage_state, load_storage_state, is_storage_state_valid,
    delete_storage_state,
)


@pytest.fixture
def settings_with_key(tmp_path):
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("storage_state_key: 'YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU='\n", encoding="utf-8")
    return str(settings_path)


def test_save_and_load_storage_state(tmp_path, settings_with_key):
    payload = {"cookies": [{"name": "PHPSESSID", "value": "xyz"}], "origins": []}
    save_storage_state(str(tmp_path), "app-1", payload, settings_path=settings_with_key)
    loaded = load_storage_state(str(tmp_path), "app-1", settings_path=settings_with_key)
    assert loaded == payload


def test_load_missing_returns_none(tmp_path, settings_with_key):
    assert load_storage_state(str(tmp_path), "nope", settings_path=settings_with_key) is None


def test_delete_storage_state(tmp_path, settings_with_key):
    save_storage_state(str(tmp_path), "app-1", {"cookies": []}, settings_path=settings_with_key)
    delete_storage_state(str(tmp_path), "app-1")
    assert load_storage_state(str(tmp_path), "app-1", settings_path=settings_with_key) is None


def test_is_storage_state_valid_uses_expiry_field():
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    fresh = Application(id="x", name="x", base_url_pattern="x", storage_state_expires_at=future, storage_state_path="x")
    expired = Application(id="x", name="x", base_url_pattern="x", storage_state_expires_at=past, storage_state_path="x")
    missing = Application(id="x", name="x", base_url_pattern="x")
    assert is_storage_state_valid(fresh) is True
    assert is_storage_state_valid(expired) is False
    assert is_storage_state_valid(missing) is False
```

- [ ] **Step 5.2: Run the tests to verify they fail**

Run: `pytest tests/test_auth_session.py -v -k "save_and_load or load_missing or delete or is_storage"`
Expected: `ImportError` on the new names.

- [ ] **Step 5.3: Implement file management**

Append to `core/auth_session.py`:
```python
from datetime import datetime, timezone
from core.applications import Application


def _state_path(data_dir: str, app_id: str) -> str:
    return os.path.join(data_dir, f"{app_id}.enc")


def save_storage_state(
    data_dir: str, app_id: str, payload: dict, *, settings_path: str = DEFAULT_SETTINGS_PATH
) -> str:
    os.makedirs(data_dir, exist_ok=True)
    blob = encrypt_storage_state(payload, settings_path=settings_path)
    path = _state_path(data_dir, app_id)
    with open(path, "wb") as f:
        f.write(blob)
    return path


def load_storage_state(
    data_dir: str, app_id: str, *, settings_path: str = DEFAULT_SETTINGS_PATH
) -> dict | None:
    path = _state_path(data_dir, app_id)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        blob = f.read()
    return decrypt_storage_state(blob, settings_path=settings_path)


def delete_storage_state(data_dir: str, app_id: str) -> None:
    path = _state_path(data_dir, app_id)
    if os.path.exists(path):
        os.remove(path)


def is_storage_state_valid(app: Application) -> bool:
    """Best-effort expiry check using the stored expiry timestamp.

    Returns False if expiry is missing, malformed, or in the past.
    A True result is only an upper bound — the server may have invalidated
    the session early. Replay handles that case by detecting 401/redirect
    to login and forcing a refresh.
    """
    if not app.storage_state_path or not app.storage_state_expires_at:
        return False
    try:
        expires = datetime.fromisoformat(app.storage_state_expires_at)
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > datetime.now(timezone.utc)
```

- [ ] **Step 5.4: Run the tests to verify they pass**

Run: `pytest tests/test_auth_session.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5.5: Add `data/storage_states/` to `.gitignore` and commit**

Append to `.gitignore` (check first that it isn't already present):
```
data/storage_states/
data/applications/
```

Then:
```bash
git add core/auth_session.py tests/test_auth_session.py .gitignore
git commit -m "feat(auth): storageState file management with expiry check"
```

---

## Task 6: Capture engine — `inject.js` with fingerprint extraction + event listeners

The injected JS is the heart of the capture engine. It runs in every page (via `page.add_init_script()`), installs event listeners, and emits events to Python via `window.__sha_record(payload)`.

Per spec §6 — this plan implements fingerprint extraction + event capture only. MutationObserver-based DOM diff capture is deferred.

**Files:**
- Create: `core/capture/__init__.py` (empty package marker)
- Create: `core/capture/inject.js`
- Create: `tests/test_capture_inject.py`

- [ ] **Step 6.1: Create the package marker**

```bash
mkdir -p core/capture
```

Create `core/capture/__init__.py`:
```python
"""Capture engine — JS injection + helpers."""
import os

INJECT_JS_PATH = os.path.join(os.path.dirname(__file__), "inject.js")


def load_inject_js() -> str:
    with open(INJECT_JS_PATH, encoding="utf-8") as f:
        return f.read()
```

- [ ] **Step 6.2: Write the failing test for fingerprint extraction**

In `tests/test_capture_inject.py`:
```python
import os
import pytest
from playwright.async_api import async_playwright
from core.capture import load_inject_js


@pytest.fixture
def sample_form_url():
    return "file://" + os.path.abspath("test_form/sample_form.html").replace("\\", "/")


@pytest.mark.asyncio
async def test_fingerprint_extraction_on_input_field(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        await ctx.add_init_script(load_inject_js())
        page = await ctx.new_page()
        await page.goto(sample_form_url)
        # Pick any input on the form; sample_form.html has at least an
        # input with id="username".
        fp = await page.evaluate(
            """() => window.__sha.buildFingerprint(document.querySelector('input'))"""
        )
        assert isinstance(fp["id"], str)
        assert "primary_locator" in fp
        assert "attributes" in fp
        assert fp["attributes"]["tag"] == "input"
        await browser.close()


@pytest.mark.asyncio
async def test_fingerprint_dedup_same_id_for_same_element(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        await ctx.add_init_script(load_inject_js())
        page = await ctx.new_page()
        await page.goto(sample_form_url)
        fp1 = await page.evaluate(
            """() => window.__sha.buildFingerprint(document.querySelector('input'))"""
        )
        fp2 = await page.evaluate(
            """() => window.__sha.buildFingerprint(document.querySelector('input'))"""
        )
        assert fp1["id"] == fp2["id"]
        await browser.close()


@pytest.mark.asyncio
async def test_event_listeners_emit_to_record_fn(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        await ctx.add_init_script(load_inject_js())
        page = await ctx.new_page()
        captured: list[dict] = []
        await page.expose_function("__sha_record", lambda payload: captured.append(payload))
        await page.goto(sample_form_url)
        # Re-init now that __sha_record exists on this page.
        await page.evaluate("() => window.__sha.attachListeners()")
        await page.fill("input", "hello")
        await page.evaluate("() => document.querySelector('input').dispatchEvent(new Event('change', {bubbles:true}))")
        # Give the page a microtask to flush.
        await page.wait_for_timeout(50)
        actions = [c["action"] for c in captured]
        assert "fill" in actions or "input" in actions
        await browser.close()
```

- [ ] **Step 6.3: Run the tests to verify they fail**

Run: `pytest tests/test_capture_inject.py -v`
Expected: failures because `inject.js` doesn't exist yet.

- [ ] **Step 6.4: Implement `inject.js`**

In `core/capture/inject.js`:
```javascript
// Capture engine — injected into every page of a recording context via
// playwright.Page.add_init_script(). Exposes window.__sha which Python
// drives via page.evaluate(). When __sha_record(payload) exists on
// window (Playwright exposes it via page.expose_function), interaction
// events stream to it.
(function () {
  if (window.__sha) return;

  // --- ID assignment + dedup ----------------------------------------
  // Same physical element -> same id across every event. Dedup key is
  // (xpath || css path) + a short hash of nearby siblings' tag+id+name.
  const idCache = new WeakMap();
  let idSeq = 0;
  function elementId(el) {
    let id = idCache.get(el);
    if (id) return id;
    const sig = xpathOf(el) + "|" + neighborhoodSignature(el);
    // First time we encounter this signature, mint a new id.
    if (!window.__sha._sigToId.has(sig)) {
      idSeq += 1;
      window.__sha._sigToId.set(sig, "el-" + idSeq);
    }
    id = window.__sha._sigToId.get(sig);
    idCache.set(el, id);
    return id;
  }

  // --- Locator strategies ------------------------------------------
  function pickPrimaryLocator(el) {
    if (el.id) return { strategy: "id", value: el.id };
    const dtid = el.getAttribute("data-testid");
    if (dtid) return { strategy: "data-testid", value: dtid };
    const name = el.getAttribute("name");
    if (name) return { strategy: "name", value: name };
    return { strategy: "css", value: cssPathOf(el) };
  }

  function fallbackLocators(el) {
    const out = [];
    if (el.id) out.push({ strategy: "id", value: el.id });
    const dtid = el.getAttribute("data-testid");
    if (dtid) out.push({ strategy: "data-testid", value: dtid });
    const name = el.getAttribute("name");
    if (name) out.push({ strategy: "name", value: name });
    out.push({ strategy: "css", value: cssPathOf(el) });
    out.push({ strategy: "xpath", value: xpathOf(el) });
    // Dedup against primary
    const primary = pickPrimaryLocator(el);
    return out.filter((x) => !(x.strategy === primary.strategy && x.value === primary.value));
  }

  // --- Path helpers -------------------------------------------------
  function cssPathOf(el) {
    if (!(el instanceof Element)) return "";
    const path = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
      let part = cur.nodeName.toLowerCase();
      if (cur.id) {
        part += "#" + CSS.escape(cur.id);
        path.unshift(part);
        break;
      } else {
        let n = 1, sib = cur.previousElementSibling;
        while (sib) {
          if (sib.nodeName === cur.nodeName) n += 1;
          sib = sib.previousElementSibling;
        }
        part += ":nth-of-type(" + n + ")";
      }
      path.unshift(part);
      cur = cur.parentElement;
    }
    return path.join(" > ");
  }

  function xpathOf(el) {
    if (!(el instanceof Element)) return "";
    if (el.id) return "//*[@id='" + el.id + "']";
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1) {
      let n = 1, sib = cur.previousElementSibling;
      while (sib) {
        if (sib.nodeName === cur.nodeName) n += 1;
        sib = sib.previousElementSibling;
      }
      parts.unshift(cur.nodeName.toLowerCase() + "[" + n + "]");
      cur = cur.parentElement;
    }
    return "/" + parts.join("/");
  }

  function neighborhoodSignature(el) {
    const parts = [];
    let sib = el.previousElementSibling;
    for (let i = 0; i < 3 && sib; i++) {
      parts.push(sib.nodeName.toLowerCase() + "[" + (sib.id || sib.getAttribute("name") || "") + "]");
      sib = sib.previousElementSibling;
    }
    sib = el.nextElementSibling;
    for (let i = 0; i < 3 && sib; i++) {
      parts.push(sib.nodeName.toLowerCase() + "[" + (sib.id || sib.getAttribute("name") || "") + "]");
      sib = sib.nextElementSibling;
    }
    return parts.join("|");
  }

  // --- Label / context discovery ------------------------------------
  function nearestLabelText(el) {
    if (el.id) {
      const lbl = document.querySelector("label[for='" + CSS.escape(el.id) + "']");
      if (lbl) return (lbl.textContent || "").trim();
    }
    let p = el.parentElement;
    for (let i = 0; i < 4 && p; i++) {
      if (p.tagName === "LABEL") return (p.textContent || "").trim();
      p = p.parentElement;
    }
    return "";
  }

  function nearestLandmarkText(el) {
    let p = el.parentElement;
    while (p) {
      if (p.matches && p.matches("fieldset, section, [role=group], h1,h2,h3,h4,h5,h6")) {
        const legend = p.querySelector ? p.querySelector("legend, h1,h2,h3,h4,h5,h6") : null;
        return ((legend || p).textContent || "").trim().slice(0, 80);
      }
      p = p.parentElement;
    }
    return "";
  }

  // --- Fingerprint construction -------------------------------------
  function buildFingerprint(el) {
    if (!el) return null;
    const id = elementId(el);
    const rect = el.getBoundingClientRect();
    const attrs = {
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute("type") || "",
      id: el.id || "",
      name: el.getAttribute("name") || "",
      class: el.getAttribute("class") || "",
      placeholder: el.getAttribute("placeholder") || "",
      aria_label: el.getAttribute("aria-label") || "",
      role: el.getAttribute("role") || "",
      text_content: (el.textContent || "").trim().slice(0, 80),
      nearest_label_text: nearestLabelText(el),
      nearest_landmark_text: nearestLandmarkText(el),
      bbox: { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) },
      html5_constraints: {
        pattern: el.getAttribute("pattern") || "",
        required: el.hasAttribute("required"),
        maxlength: el.getAttribute("maxlength") || "",
        minlength: el.getAttribute("minlength") || "",
        min: el.getAttribute("min") || "",
        max: el.getAttribute("max") || "",
      },
      autocomplete: el.getAttribute("autocomplete") || "",
      xpath: xpathOf(el),
      css_path: cssPathOf(el),
      neighborhood_signature: neighborhoodSignature(el),
    };
    return {
      id,
      primary_locator: pickPrimaryLocator(el),
      fallback_locators: fallbackLocators(el),
      attributes: attrs,
      page_context: {
        url: location.href,
        section_label: nearestLandmarkText(el),
      },
    };
  }

  // --- Event handling -----------------------------------------------
  function emit(action, el, value) {
    if (!window.__sha_record) return;
    const fp = buildFingerprint(el);
    window.__sha_record({
      action,
      element: fp,
      value: value == null ? null : String(value),
      timestamp_ms: Date.now() - window.__sha._startTs,
      url: location.href,
    });
  }

  function attachListeners() {
    if (window.__sha._attached) return;
    window.__sha._attached = true;

    document.addEventListener("change", (ev) => {
      const el = ev.target;
      if (!(el instanceof HTMLElement)) return;
      const tag = el.tagName;
      if (tag === "INPUT") {
        const type = (el.getAttribute("type") || "text").toLowerCase();
        if (type === "checkbox") return emit(el.checked ? "check" : "uncheck", el, null);
        if (type === "radio") return emit("check", el, el.value);
        return emit("fill", el, el.value);
      }
      if (tag === "TEXTAREA") return emit("fill", el, el.value);
      if (tag === "SELECT") return emit("select", el, el.value);
    }, true);

    document.addEventListener("click", (ev) => {
      const el = ev.target.closest && ev.target.closest("button, a, [role=button], input[type=submit], input[type=button]");
      if (el) emit("click", el, null);
    }, true);

    window.addEventListener("submit", (ev) => {
      const form = ev.target;
      if (form && form.tagName === "FORM") emit("submit", form, null);
    }, true);
  }

  window.__sha = {
    _sigToId: new Map(),
    _attached: false,
    _startTs: Date.now(),
    buildFingerprint,
    attachListeners,
  };

  // Auto-attach once the DOM is ready. Python can also call
  // window.__sha.attachListeners() explicitly after exposing __sha_record.
  if (document.readyState !== "loading") attachListeners();
  else document.addEventListener("DOMContentLoaded", attachListeners);
})();
```

- [ ] **Step 6.5: Run the tests to verify they pass**

Run: `pytest tests/test_capture_inject.py -v`
Expected: all 3 tests PASS. If the third test is flaky around timing, bump `wait_for_timeout` to 200 ms.

- [ ] **Step 6.6: Commit**

```bash
git add core/capture/ tests/test_capture_inject.py
git commit -m "feat(capture): injected JS for fingerprint extraction + event capture"
```

---

## Task 7: `RecorderSession` — Python orchestration

The Python side that drives the headed browser and collects events into a `Recording`.

**Files:**
- Create: `core/recorder.py`
- Create: `tests/test_recorder.py`

- [ ] **Step 7.1: Write the failing test**

In `tests/test_recorder.py`:
```python
import os
import pytest
from core.recorder import RecorderSession


@pytest.fixture
def sample_form_url():
    return "file://" + os.path.abspath("test_form/sample_form.html").replace("\\", "/")


@pytest.mark.asyncio
async def test_recorder_captures_a_fill_event(sample_form_url):
    rec_session = RecorderSession(application_id="test-app", headless=True)
    await rec_session.start(start_url=sample_form_url)
    # Drive the page from the test (the user would do this manually in
    # real use); the recorder is paused on `start_url` waiting for events.
    await rec_session.page.fill("input", "hello world")
    await rec_session.page.evaluate(
        "() => document.querySelector('input').dispatchEvent(new Event('change', {bubbles:true}))"
    )
    await rec_session.page.wait_for_timeout(100)
    recording = await rec_session.stop(name="test recording")
    assert recording.application_id == "test-app"
    assert recording.start_url == sample_form_url
    assert recording.name == "test recording"
    assert any(s.action == "fill" and s.value == "hello world" for s in recording.steps)
    assert recording.steps[0].element is not None
    assert recording.steps[0].element.attributes["tag"] == "input"


@pytest.mark.asyncio
async def test_recorder_stops_cleanly_with_no_events(sample_form_url):
    rec_session = RecorderSession(application_id="empty-app", headless=True)
    await rec_session.start(start_url=sample_form_url)
    recording = await rec_session.stop(name="empty")
    assert recording.steps == []
```

- [ ] **Step 7.2: Run the test to verify it fails**

Run: `pytest tests/test_recorder.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 7.3: Implement `RecorderSession`**

In `core/recorder.py`:
```python
from __future__ import annotations
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from core.capture import load_inject_js
from core.recording import Recording, Step, ElementFingerprint


class RecorderSession:
    """Owns a headed Chromium recording session.

    Lifecycle:
        rec = RecorderSession(application_id="app-1")
        await rec.start(start_url="https://target/")
        # ... user drives the browser in real life ...
        recording = await rec.stop(name="Happy path")
    """

    def __init__(
        self,
        application_id: str,
        *,
        headless: bool = False,
        storage_state: dict | None = None,
    ):
        self.application_id = application_id
        self.headless = headless
        self.storage_state = storage_state
        self._pw = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._events: list[dict] = []
        self._start_url: str = ""
        self._start_ts: float = 0.0

    async def start(self, start_url: str) -> None:
        self._start_url = start_url
        self._start_ts = time.time()
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        context_kwargs = {}
        if self.storage_state:
            context_kwargs["storage_state"] = self.storage_state
        self._context = await self._browser.new_context(**context_kwargs)
        await self._context.add_init_script(load_inject_js())
        self.page = await self._context.new_page()
        await self.page.expose_function("__sha_record", self._on_event)
        await self.page.goto(start_url)
        # The page may have loaded before expose_function landed; ensure
        # listeners are attached now that __sha_record exists.
        await self.page.evaluate("() => window.__sha && window.__sha.attachListeners()")

    def _on_event(self, payload: dict) -> None:
        self._events.append(payload)

    async def stop(self, name: str) -> Recording:
        steps: list[Step] = []
        for idx, ev in enumerate(self._events):
            element = None
            fp_dict = ev.get("element")
            if fp_dict:
                element = ElementFingerprint.from_dict(fp_dict)
            steps.append(
                Step(
                    index=idx,
                    action=ev["action"],
                    element=element,
                    value=ev.get("value"),
                    timestamp_ms=int(ev.get("timestamp_ms") or 0),
                )
            )
        recording = Recording(
            id="rec-" + uuid.uuid4().hex[:8],
            name=name,
            kind="scenario",
            application_id=self.application_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            start_url=self._start_url,
            steps=steps,
            success_signal=None,
        )
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()
        return recording
```

- [ ] **Step 7.4: Run the test to verify it passes**

Run: `pytest tests/test_recorder.py -v`
Expected: both tests PASS.

- [ ] **Step 7.5: Commit**

```bash
git add core/recorder.py tests/test_recorder.py
git commit -m "feat(recorder): RecorderSession orchestrates capture into a Recording"
```

---

## Task 8: Extend `Scenario` schema for `"recorded"` kind

**Files:**
- Modify: `core/scenarios.py`
- Modify: `tests/test_scenarios.py` (create if absent)

- [ ] **Step 8.1: Write the failing test**

If `tests/test_scenarios.py` doesn't exist, create it. Otherwise append to it:

```python
import pytest
from core.scenarios import Scenario, save_scenario, load_scenario, ScenarioValidationError


def test_recorded_scenario_round_trip(tmp_path):
    sc = Scenario(
        id="sc-rec-1",
        name="KYC happy path",
        kind="recorded",
        base_url="",
        steps=[],
        dataset=[],
        expected_outcome="success",
        application_id="app-1",
        recordings=[{"id": "rec-001", "name": "Happy path"}],
        ai_test_cases=[],
    )
    save_scenario(str(tmp_path), sc)
    loaded = load_scenario(str(tmp_path), "sc-rec-1")
    assert loaded.kind == "recorded"
    assert loaded.application_id == "app-1"
    assert loaded.recordings == [{"id": "rec-001", "name": "Happy path"}]


def test_recorded_scenario_requires_application_id(tmp_path):
    sc = Scenario(
        id="sc-rec-2", name="x", kind="recorded", base_url="",
        steps=[], dataset=[], expected_outcome="success",
        application_id=None, recordings=[{"id": "r"}],
    )
    with pytest.raises(ScenarioValidationError):
        save_scenario(str(tmp_path), sc)


def test_recorded_scenario_requires_at_least_one_recording(tmp_path):
    sc = Scenario(
        id="sc-rec-3", name="x", kind="recorded", base_url="",
        steps=[], dataset=[], expected_outcome="success",
        application_id="app-1", recordings=[],
    )
    with pytest.raises(ScenarioValidationError):
        save_scenario(str(tmp_path), sc)
```

- [ ] **Step 8.2: Run the tests to verify they fail**

Run: `pytest tests/test_scenarios.py -v -k recorded`
Expected: TypeError or ValidationError (the new fields don't exist yet).

- [ ] **Step 8.3: Extend `core/scenarios.py`**

Modify `core/scenarios.py`:

Change line 7 from:
```python
VALID_KINDS = {"single-page", "multi-page"}
```
to:
```python
VALID_KINDS = {"single-page", "multi-page", "recorded"}
```

Add to the `Scenario` dataclass (after `created_at: str = ""` on line 27):
```python
    application_id: str | None = None
    recordings: list[dict] = field(default_factory=list)
    ai_test_cases: list[dict] = field(default_factory=list)
```

Extend `_validate` — after the multi-page block (after line 74), add:
```python
    if sc.kind == "recorded":
        if not sc.application_id:
            raise ScenarioValidationError("recorded scenarios require application_id")
        if not sc.recordings:
            raise ScenarioValidationError("recorded scenarios require at least one recording")
        return
```

- [ ] **Step 8.4: Run the tests to verify they pass**

Run: `pytest tests/test_scenarios.py -v -k recorded`
Expected: all 3 tests PASS.

- [ ] **Step 8.5: Commit**

```bash
git add core/scenarios.py tests/test_scenarios.py
git commit -m "feat(scenarios): add 'recorded' kind with application_id + recordings"
```

---

## Task 9: Replay — `find_element_by_fingerprint`

Replay starts with element location. Given a fingerprint, find the live element via primary → fallback chain. Healing is deferred to a later plan.

**Files:**
- Create: `core/replay.py`
- Create: `tests/test_replay.py`

- [ ] **Step 9.1: Write the failing test**

In `tests/test_replay.py`:
```python
import os
import pytest
from playwright.async_api import async_playwright
from core.recording import ElementFingerprint
from core.replay import find_element_by_fingerprint, ElementNotFound


@pytest.fixture
def sample_form_url():
    return "file://" + os.path.abspath("test_form/sample_form.html").replace("\\", "/")


@pytest.mark.asyncio
async def test_find_by_primary_id_locator(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto(sample_form_url)
        # Use the first <input>'s id from sample_form.html — known to exist.
        first_input_id = await page.evaluate("() => document.querySelector('input').id")
        if not first_input_id:
            pytest.skip("sample_form.html's first input has no id")
        fp = ElementFingerprint(
            id="el-1",
            primary_locator={"strategy": "id", "value": first_input_id},
            fallback_locators=[],
            attributes={"tag": "input"},
            page_context={"url": sample_form_url},
        )
        loc = await find_element_by_fingerprint(page, fp)
        assert await loc.count() == 1
        await browser.close()


@pytest.mark.asyncio
async def test_falls_back_when_primary_misses(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto(sample_form_url)
        first_input_name = await page.evaluate(
            "() => document.querySelector('input').getAttribute('name')"
        )
        if not first_input_name:
            pytest.skip("sample_form.html's first input has no name")
        fp = ElementFingerprint(
            id="el-2",
            primary_locator={"strategy": "id", "value": "does-not-exist-xyz"},
            fallback_locators=[{"strategy": "name", "value": first_input_name}],
            attributes={"tag": "input"},
            page_context={"url": sample_form_url},
        )
        loc = await find_element_by_fingerprint(page, fp)
        assert await loc.count() == 1
        await browser.close()


@pytest.mark.asyncio
async def test_raises_when_nothing_matches(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto(sample_form_url)
        fp = ElementFingerprint(
            id="el-3",
            primary_locator={"strategy": "id", "value": "nope"},
            fallback_locators=[{"strategy": "name", "value": "also-nope"}],
            attributes={"tag": "input"},
            page_context={"url": sample_form_url},
        )
        with pytest.raises(ElementNotFound):
            await find_element_by_fingerprint(page, fp)
        await browser.close()
```

- [ ] **Step 9.2: Run the tests to verify they fail**

Run: `pytest tests/test_replay.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 9.3: Implement `find_element_by_fingerprint`**

In `core/replay.py`:
```python
from __future__ import annotations
from playwright.async_api import Page, Locator

from core.recording import ElementFingerprint


class ElementNotFound(RuntimeError):
    """Raised when no locator (primary or fallback) matches a fingerprint."""


def _locator_for(page: Page, locator: dict) -> Locator:
    strategy = locator["strategy"]
    value = locator["value"]
    if strategy == "id":
        return page.locator(f"#{value}")
    if strategy == "data-testid":
        return page.locator(f"[data-testid='{value}']")
    if strategy == "name":
        return page.locator(f"[name='{value}']")
    if strategy == "css":
        return page.locator(value)
    if strategy == "xpath":
        return page.locator(f"xpath={value}")
    raise ValueError(f"unknown locator strategy: {strategy!r}")


async def find_element_by_fingerprint(page: Page, fp: ElementFingerprint) -> Locator:
    """Try the primary locator, then each fallback. Return the first match.

    Match means `count() == 1` — strict single match. If primary matches
    multiple, we still take it (Playwright will use .first() at the action
    site), but we prefer locators that resolve to exactly one element.
    """
    candidates = [fp.primary_locator, *fp.fallback_locators]
    last_err: Exception | None = None
    for loc_dict in candidates:
        try:
            loc = _locator_for(page, loc_dict)
            if await loc.count() >= 1:
                return loc
        except Exception as e:
            last_err = e
            continue
    raise ElementNotFound(
        f"no locator matched for fingerprint {fp.id}; tried {len(candidates)} strategies"
        + (f"; last error: {last_err}" if last_err else "")
    )
```

- [ ] **Step 9.4: Run the tests to verify they pass**

Run: `pytest tests/test_replay.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 9.5: Commit**

```bash
git add core/replay.py tests/test_replay.py
git commit -m "feat(replay): find_element_by_fingerprint with primary->fallback chain"
```

---

## Task 10: Replay — `execute_step`

Given a Step + an open Page, perform the recorded action.

**Files:**
- Modify: `core/replay.py`
- Modify: `tests/test_replay.py`

- [ ] **Step 10.1: Write the failing test**

Append to `tests/test_replay.py`:
```python
from core.recording import Step
from core.replay import execute_step


def _fp_for_input_name(name: str, url: str) -> ElementFingerprint:
    return ElementFingerprint(
        id="el-x",
        primary_locator={"strategy": "name", "value": name},
        fallback_locators=[],
        attributes={"tag": "input"},
        page_context={"url": url},
    )


@pytest.mark.asyncio
async def test_execute_fill_step(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto(sample_form_url)
        name = await page.evaluate(
            "() => document.querySelector('input').getAttribute('name')"
        )
        if not name:
            pytest.skip("sample form has no named input")
        step = Step(index=0, action="fill", element=_fp_for_input_name(name, sample_form_url), value="ACME-42")
        await execute_step(page, step, override=None)
        actual = await page.eval_on_selector(f"[name='{name}']", "el => el.value")
        assert actual == "ACME-42"
        await browser.close()


@pytest.mark.asyncio
async def test_execute_fill_with_override(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto(sample_form_url)
        name = await page.evaluate(
            "() => document.querySelector('input').getAttribute('name')"
        )
        if not name:
            pytest.skip("sample form has no named input")
        step = Step(index=0, action="fill", element=_fp_for_input_name(name, sample_form_url), value="recorded")
        await execute_step(page, step, override="OVERRIDDEN")
        actual = await page.eval_on_selector(f"[name='{name}']", "el => el.value")
        assert actual == "OVERRIDDEN"
        await browser.close()
```

- [ ] **Step 10.2: Run the tests to verify they fail**

Run: `pytest tests/test_replay.py -v -k execute`
Expected: ImportError on `execute_step`.

- [ ] **Step 10.3: Implement `execute_step`**

Append to `core/replay.py`:
```python
from core.recording import Step


async def execute_step(page: Page, step: Step, override: str | None) -> None:
    """Run one recorded step against `page`.

    `override` lets callers (test-case replay) substitute a different value
    for the same step without mutating the Recording. If None, the step's
    recorded value is used.
    """
    value = override if override is not None else step.value
    if step.action == "navigate":
        await page.goto(value or "")
        return
    if step.action == "wait":
        await page.wait_for_timeout(int(value or 0))
        return
    if step.element is None:
        raise ValueError(f"step {step.index} action={step.action!r} requires an element fingerprint")
    loc = await find_element_by_fingerprint(page, step.element)
    if step.action == "fill":
        await loc.first.fill(value or "")
    elif step.action == "click" or step.action == "submit":
        await loc.first.click()
    elif step.action == "select":
        await loc.first.select_option(value or "")
    elif step.action == "check":
        await loc.first.check()
    elif step.action == "uncheck":
        await loc.first.uncheck()
    elif step.action == "press":
        await loc.first.press(value or "")
    else:
        raise ValueError(f"unsupported action: {step.action!r}")
```

- [ ] **Step 10.4: Run the tests to verify they pass**

Run: `pytest tests/test_replay.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 10.5: Commit**

```bash
git add core/replay.py tests/test_replay.py
git commit -m "feat(replay): execute_step with optional value override"
```

---

## Task 11: Replay — `replay_recording` end-to-end

Pull it together: open a context (with storageState if available), navigate, walk every step.

**Files:**
- Modify: `core/replay.py`
- Modify: `tests/test_replay.py`

- [ ] **Step 11.1: Write the failing test**

Append to `tests/test_replay.py`:
```python
from core.recording import Recording
from core.replay import replay_recording, ReplayOutcome


@pytest.mark.asyncio
async def test_replay_recording_walks_all_steps(sample_form_url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        await page.goto(sample_form_url)
        name = await page.evaluate(
            "() => document.querySelector('input').getAttribute('name')"
        )
        if not name:
            pytest.skip("sample form has no named input")
        await browser.close()

    fp = ElementFingerprint(
        id="el-x",
        primary_locator={"strategy": "name", "value": name},
        fallback_locators=[],
        attributes={"tag": "input"},
        page_context={"url": sample_form_url},
    )
    recording = Recording(
        id="rec-test", name="t", kind="scenario", application_id="app-1",
        created_at="", start_url=sample_form_url,
        steps=[Step(index=0, action="fill", element=fp, value="hello")],
    )
    outcome = await replay_recording(recording, headless=True)
    assert outcome.completed_steps == 1
    assert outcome.failed_step_index is None
    assert outcome.error is None


@pytest.mark.asyncio
async def test_replay_recording_reports_failed_step(sample_form_url):
    bad_fp = ElementFingerprint(
        id="el-x",
        primary_locator={"strategy": "id", "value": "no-such-element"},
        fallback_locators=[],
        attributes={"tag": "input"},
        page_context={"url": sample_form_url},
    )
    recording = Recording(
        id="rec-test", name="t", kind="scenario", application_id="app-1",
        created_at="", start_url=sample_form_url,
        steps=[Step(index=0, action="fill", element=bad_fp, value="x")],
    )
    outcome = await replay_recording(recording, headless=True)
    assert outcome.failed_step_index == 0
    assert outcome.error is not None
```

- [ ] **Step 11.2: Run the tests to verify they fail**

Run: `pytest tests/test_replay.py -v -k replay_recording`
Expected: ImportError on `replay_recording` and `ReplayOutcome`.

- [ ] **Step 11.3: Implement `replay_recording`**

Append to `core/replay.py`:
```python
from dataclasses import dataclass, field
from typing import Optional
from playwright.async_api import async_playwright

from core.recording import Recording


@dataclass
class ReplayOutcome:
    completed_steps: int = 0
    failed_step_index: Optional[int] = None
    error: Optional[str] = None
    final_url: str = ""


async def replay_recording(
    recording: Recording,
    *,
    data_overrides: dict[str, str] | None = None,
    storage_state: dict | None = None,
    headless: bool = True,
) -> ReplayOutcome:
    """Open a context, navigate to start_url, walk every step.

    `data_overrides` maps `ElementFingerprint.id` -> override value. Used by
    test cases; falls back to each step's recorded value when absent.
    """
    overrides = data_overrides or {}
    outcome = ReplayOutcome()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx_kwargs = {}
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state
        context = await browser.new_context(**ctx_kwargs)
        page = await context.new_page()
        try:
            await page.goto(recording.start_url)
            for step in recording.steps:
                ovr = None
                if step.element is not None:
                    ovr = overrides.get(step.element.id)
                try:
                    await execute_step(page, step, override=ovr)
                    outcome.completed_steps += 1
                except Exception as e:
                    outcome.failed_step_index = step.index
                    outcome.error = f"{type(e).__name__}: {e}"
                    break
            outcome.final_url = page.url
        finally:
            await context.close()
            await browser.close()
    return outcome
```

- [ ] **Step 11.4: Run the tests to verify they pass**

Run: `pytest tests/test_replay.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 11.5: Commit**

```bash
git add core/replay.py tests/test_replay.py
git commit -m "feat(replay): replay_recording walks all steps with optional overrides"
```

---

## Task 12: Recorder CLI subprocess entry point

Streamlit reruns its script on every interaction, creating a fresh event loop each time. That makes keeping an async Playwright browser open across UI interactions unreliable. Solution: the UI spawns a **subprocess** that owns the browser; the user ends the recording by **closing the browser window**; the subprocess writes the Recording (and login-success candidates) to disk; Streamlit polls for the output file.

This task builds the CLI; Task 13 wires the UI to it.

**Files:**
- Create: `core/recorder_cli.py`
- Create: `tests/test_recorder_cli.py`

- [ ] **Step 12.1: Write the failing test**

In `tests/test_recorder_cli.py`:
```python
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import pytest


@pytest.mark.skipif(os.environ.get("CI") == "true", reason="needs interactive close — skipped in CI")
def test_recorder_cli_smoke_run(tmp_path):
    """Smoke test: launch the CLI, immediately kill the browser, verify the
    output file is written.

    This test depends on a human (or test fixture) closing the launched
    Chromium. To keep the test automatable, we use --auto-close=N to make
    the CLI auto-stop after N milliseconds of recording.
    """
    sample = "file://" + os.path.abspath("test_form/sample_form.html").replace("\\", "/")
    out_recording = tmp_path / "rec.yaml"
    out_candidates = tmp_path / "candidates.json"
    proc = subprocess.run(
        [
            sys.executable, "-m", "core.recorder_cli",
            "--app-id", "test-app",
            "--start-url", sample,
            "--output-recording", str(out_recording),
            "--output-candidates", str(out_candidates),
            "--auto-close-ms", "1500",
            "--headless", "true",
        ],
        capture_output=True, timeout=60, text=True,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    assert out_recording.exists()
    assert out_candidates.exists()
    candidates_data = json.loads(out_candidates.read_text(encoding="utf-8"))
    assert "candidates" in candidates_data
    assert "final_url" in candidates_data
```

- [ ] **Step 12.2: Run the test to verify it fails**

Run: `pytest tests/test_recorder_cli.py -v`
Expected: `ModuleNotFoundError` (no `core.recorder_cli` yet).

- [ ] **Step 12.3: Implement `core/recorder_cli.py`**

```python
"""CLI entry point for a recording session.

Invoked as a subprocess from the Streamlit UI. Owns its own asyncio loop
so it stays orthogonal to Streamlit's rerun cycle. Records until either:
  - the user closes the Chromium window (production use), or
  - --auto-close-ms elapses (test mode).

On stop, writes:
  - <output-recording>:  the Recording YAML
  - <output-candidates>: a JSON blob with the success-signal candidates
                         (top-N distinctive elements on the page at stop time)
                         and the final URL.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from pathlib import Path

from core.recorder import RecorderSession
from core.recording import save_recording


async def _candidates_from_page(page) -> list[dict]:
    return await page.evaluate(
        """(maxN) => {
            const seen = new Set();
            const out = [];
            const all = document.querySelectorAll('a, button, [role=button], [data-testid], [aria-label], input, select');
            for (const el of all) {
                const rect = el.getBoundingClientRect();
                if (rect.width < 10 || rect.height < 10) continue;
                if (!window.__sha) continue;
                const fp = window.__sha.buildFingerprint(el);
                if (seen.has(fp.id)) continue;
                seen.add(fp.id);
                const score =
                    (fp.attributes.id ? 4 : 0) +
                    (fp.attributes.aria_label ? 3 : 0) +
                    ((fp.attributes.text_content || '').length > 0 ? 1 : 0);
                out.push({fp, score});
                if (out.length >= 50) break;
            }
            out.sort((a,b) => b.score - a.score);
            return out.slice(0, maxN).map(c => c.fp);
        }""",
        5,
    )


async def _run(args: argparse.Namespace) -> int:
    storage_state = None
    if args.storage_state_path:
        p = Path(args.storage_state_path)
        if p.exists():
            storage_state = json.loads(p.read_text(encoding="utf-8"))

    session = RecorderSession(
        application_id=args.app_id,
        headless=(args.headless.lower() == "true"),
        storage_state=storage_state,
    )
    await session.start(start_url=args.start_url)

    if args.auto_close_ms and args.auto_close_ms > 0:
        await session.page.wait_for_timeout(args.auto_close_ms)
    else:
        # Wait until the user closes the window.
        while not session.page.is_closed():
            await asyncio.sleep(0.5)

    # Capture candidates BEFORE stopping (stop closes the page).
    candidates: list[dict] = []
    final_url = ""
    try:
        if not session.page.is_closed():
            final_url = session.page.url
            candidates = await _candidates_from_page(session.page)
    except Exception:
        pass  # page closed unexpectedly; candidates stay empty

    recording = await session.stop(name=args.name or "untitled")

    Path(args.output_recording).parent.mkdir(parents=True, exist_ok=True)
    save_recording(args.output_recording, recording)

    Path(args.output_candidates).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output_candidates, "w", encoding="utf-8") as f:
        json.dump({"candidates": candidates, "final_url": final_url}, f)

    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scenario recording CLI")
    ap.add_argument("--app-id", required=True)
    ap.add_argument("--start-url", required=True)
    ap.add_argument("--output-recording", required=True)
    ap.add_argument("--output-candidates", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--storage-state-path", default="")
    ap.add_argument("--headless", default="false", help="'true' or 'false'")
    ap.add_argument("--auto-close-ms", type=int, default=0,
                    help="auto-stop after N ms (test mode); 0 = wait for user to close window")
    args = ap.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 12.4: Run the test to verify it passes**

Run: `pytest tests/test_recorder_cli.py -v`
Expected: PASS (the test uses `--auto-close-ms 1500`, so no human is needed).

- [ ] **Step 12.5: Commit**

```bash
git add core/recorder_cli.py tests/test_recorder_cli.py
git commit -m "feat(recorder): CLI subprocess entry point with candidate snapshot"
```

---

## Task 13: Streamlit UI — applications page + login recording flow

Spawn the recorder CLI as a subprocess. User closes the Chromium window when done. Streamlit polls for the output files.

**Files:**
- Create: `pages/6_recordings.py`
- Create: `ui/recording/__init__.py` (empty)
- Create: `ui/recording/success_signal_picker.py`

UI tasks are verified by manual smoke test; the underlying CLI and data-model paths are unit-tested.

- [ ] **Step 13.1: Create the package marker**

`ui/recording/__init__.py`:
```python
"""Streamlit components for the recording flow."""
```

- [ ] **Step 13.2: Implement the success signal picker component**

In `ui/recording/success_signal_picker.py`:
```python
"""Renders a checkbox list of candidate elements (captured by the
recorder CLI when the user closed the browser) and returns a
SuccessSignal when the user confirms."""
from __future__ import annotations
from datetime import datetime, timezone
import streamlit as st

from core.recording import ElementFingerprint, SuccessSignal


def render_picker(candidates: list[dict], url: str, key_prefix: str = "ss") -> SuccessSignal | None:
    st.markdown("**Confirm what proves you're logged in.** Pick one or more elements that should be visible on a logged-in page:")
    url_pattern = st.text_input(
        "URL contains (substring)",
        value=url.split("?")[0].split("#")[0],
        key=f"{key_prefix}_url",
    )
    picks: list[ElementFingerprint] = []
    for i, fp_dict in enumerate(candidates):
        fp = ElementFingerprint.from_dict(fp_dict)
        label_text = (
            fp.attributes.get("aria_label")
            or fp.attributes.get("text_content")
            or fp.attributes.get("nearest_label_text")
            or fp.primary_locator["value"]
        )[:80]
        if st.checkbox(f"Element: {label_text!r}", key=f"{key_prefix}_cand_{i}"):
            picks.append(fp)
    if st.button("Confirm signal", key=f"{key_prefix}_confirm"):
        return SuccessSignal(
            url_pattern=url_pattern,
            required_elements=picks,
            forbidden_elements=[],
            captured_at=datetime.now(timezone.utc).isoformat(),
        )
    return None
```

- [ ] **Step 13.3: Implement `pages/6_recordings.py`**

```python
"""Manage applications + login recordings.

Recording is run in a subprocess. The user closes the browser window
to end the recording; this page polls for the output files.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import streamlit as st

from core.applications import (
    Application, save_application, list_applications, delete_application, load_application,
)
from core.auth_session import save_storage_state, is_storage_state_valid
from core.recording import load_recording, save_recording
from ui.recording.success_signal_picker import render_picker

APP_DIR = "data/applications"
STATE_DIR = "data/storage_states"
WORK_DIR = "data/recorder_work"

st.set_page_config(page_title="Recordings", page_icon="🎬")
st.title("Applications & Login Recordings")

# --- Applications list -------------------------------------------------
st.subheader("Applications")
apps = list_applications(APP_DIR)
for app in apps:
    cols = st.columns([3, 2, 2, 1])
    cols[0].write(f"**{app.name}** — `{app.base_url_pattern}`")
    cols[1].write("login ✓" if app.login_recording_id else "login ✗")
    health = "🟢" if is_storage_state_valid(app) else "🔴"
    cols[2].write(f"state {health}")
    if cols[3].button("Delete", key=f"del-{app.id}"):
        delete_application(APP_DIR, app.id)
        st.rerun()

st.divider()
st.subheader("New application")

with st.form("new_app"):
    name = st.text_input("Name")
    login_url = st.text_input("Login URL")
    submitted = st.form_submit_button("Create + record login")

if submitted and name and login_url:
    app = Application(
        id="app-" + uuid.uuid4().hex[:8],
        name=name,
        base_url_pattern=login_url,
    )
    save_application(APP_DIR, app)
    st.session_state["login_app_id"] = app.id
    st.session_state["login_url"] = login_url
    st.rerun()

# --- Login recording flow ---------------------------------------------
app_id = st.session_state.get("login_app_id")
if app_id:
    st.divider()
    st.subheader(f"Recording login for {app_id}")
    Path(WORK_DIR).mkdir(parents=True, exist_ok=True)
    rec_path = os.path.join(WORK_DIR, f"{app_id}_login.yaml")
    cand_path = os.path.join(WORK_DIR, f"{app_id}_candidates.json")

    if "login_proc_pid" not in st.session_state:
        st.info("Click Start to open a browser. Sign in normally. **Close the browser window** when you're on a logged-in page — that ends the recording.")
        if st.button("Start"):
            for p in (rec_path, cand_path):
                if os.path.exists(p):
                    os.remove(p)
            proc = subprocess.Popen(
                [
                    sys.executable, "-m", "core.recorder_cli",
                    "--app-id", app_id,
                    "--start-url", st.session_state["login_url"],
                    "--output-recording", rec_path,
                    "--output-candidates", cand_path,
                    "--name", f"login: {app_id}",
                    "--headless", "false",
                ]
            )
            st.session_state["login_proc_pid"] = proc.pid
            st.rerun()
    else:
        proc_done = (
            os.path.exists(rec_path) and os.path.exists(cand_path)
        )
        if not proc_done:
            st.warning("Recording in progress. Close the browser window when done, then click Refresh.")
            if st.button("Refresh"):
                st.rerun()
        else:
            cand_data = json.loads(Path(cand_path).read_text(encoding="utf-8"))
            signal = render_picker(
                cand_data["candidates"],
                cand_data["final_url"] or st.session_state["login_url"],
                key_prefix=f"ss_{app_id}",
            )
            if signal is not None:
                login_rec = load_recording(rec_path)
                login_rec.kind = "login"
                login_rec.success_signal = signal
                target = os.path.join(APP_DIR, app_id, "login_recording.yaml")
                save_recording(target, login_rec)

                # storageState is captured by the CLI at session start; for
                # MVP we re-extract by running a quick storageState dump.
                # Simpler: have the CLI write storageState alongside. For
                # this plan, we rely on the user re-running login when
                # the first replay fails auth (acceptable for the demo).
                # NOTE: production should have the CLI write storageState
                # to a known path and have this page encrypt it.

                app = load_application(APP_DIR, app_id)
                app.login_recording_id = login_rec.id
                now = datetime.now(timezone.utc)
                app.storage_state_captured_at = now.isoformat()
                app.storage_state_expires_at = (now + timedelta(hours=12)).isoformat()
                save_application(APP_DIR, app)

                for k in ("login_proc_pid", "login_app_id", "login_url"):
                    st.session_state.pop(k, None)
                st.success(f"Login recorded for {app.name}.")
                st.rerun()
```

> **Note on storageState export:** the CLI in Task 12 does NOT currently export storageState. The minimum demoable flow re-records login when replay fails auth. For a polished build, extend `core/recorder_cli.py` with a `--output-storage-state <path>` argument that calls `await session._context.storage_state()` before stopping; then in this page, encrypt that file with `save_storage_state`. Treat this extension as a follow-up step inside this task once the rest works.

- [ ] **Step 13.4: Extend the CLI to export storageState**

Modify `core/recorder_cli.py`:

Add to `main()`'s argparse block:
```python
ap.add_argument("--output-storage-state", default="")
```

Modify `_run()` — before `recording = await session.stop(...)`, capture state:
```python
state_payload = None
if args.output_storage_state and session._context:
    try:
        state_payload = await session._context.storage_state()
    except Exception:
        state_payload = None
```

After `save_recording(...)`, add:
```python
if state_payload is not None and args.output_storage_state:
    Path(args.output_storage_state).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output_storage_state, "w", encoding="utf-8") as f:
        json.dump(state_payload, f)
```

Update the page in Step 13.3 to pass `--output-storage-state` and to encrypt the file after the run:
```python
state_path = os.path.join(WORK_DIR, f"{app_id}_state.json")
# add to subprocess args:
#   "--output-storage-state", state_path,
```

In the "if signal is not None" branch, before clearing session state:
```python
if os.path.exists(state_path):
    payload = json.loads(Path(state_path).read_text(encoding="utf-8"))
    save_storage_state(STATE_DIR, app_id, payload)
    app.storage_state_path = os.path.join(STATE_DIR, app_id + ".enc")
    save_application(APP_DIR, app)
    os.remove(state_path)
```

- [ ] **Step 13.5: Manual smoke test**

1. Generate a Fernet key and add it to `data/settings.yaml`:
   ```bash
   python -c "from cryptography.fernet import Fernet; print('storage_state_key:', Fernet.generate_key().decode())"
   ```
   Append the output line to `data/settings.yaml`.

2. Run: `streamlit run app.py`

3. Navigate to **Recordings**. Fill the "New application" form: Name = "Test Form", Login URL = `file:///<absolute-path>/test_form/sample_form.html`. Click "Create + record login."

4. Click **Start**. A Chromium window opens at the sample form. There's no login on this form, so just close the window immediately.

5. Click **Refresh** in Streamlit. The candidates picker should appear with 5 elements from the form. Check one or two, click "Confirm signal."

6. The application card should now show "login ✓" and a 🟢 health indicator. Verify `data/applications/<app-id>/login_recording.yaml` and `data/storage_states/<app-id>.enc` exist on disk.

- [ ] **Step 13.6: Commit**

```bash
git add -f pages/6_recordings.py ui/recording/ core/recorder_cli.py
git commit -m "feat(ui): applications page + subprocess-driven login recording flow"
```

---

## Task 14: Streamlit UI — record-a-scenario entry point

Wire "Record a Scenario" into scenario creation, using the same subprocess pattern as Task 13.

**Files:**
- Modify: `ui/scenarios/<scenario-creation-file>.py` — add "recorded" kind option (locate via grep in Step 14.1)
- Modify: `ui/scenarios/detail.py` — show a Recordings list and a "Start new recording" subprocess flow for `kind="recorded"` scenarios

- [ ] **Step 14.1: Locate the scenario creation form**

Run: `grep -rn "single-page\|multi-page" ui/ pages/ --include="*.py" | grep -i "radio\|selectbox\|kind" | head -10`

Expected: identifies the file (likely under `ui/scenarios/`) where the user picks the scenario kind via a radio or selectbox. Read that file and note the function that renders the form. Use that file path in Step 14.2.

Also run: `grep -n "detail" ui/scenarios/*.py | head -10` to confirm the scenario detail file path (spec implies `ui/scenarios/detail.py`).

- [ ] **Step 14.2: Add "recorded" as a kind option in the creation form**

In the file located in Step 14.1, add `"recorded"` to the kind radio. When the user picks `"recorded"`:

```python
import uuid
from core.applications import list_applications
from core.scenarios import Scenario, save_scenario

# ... inside the kind=="recorded" branch:
apps = list_applications("data/applications")
if not apps:
    st.warning("Create an application on the Recordings page first.")
else:
    app_id = st.selectbox(
        "Application",
        [a.id for a in apps],
        format_func=lambda i: next(a.name for a in apps if a.id == i),
        key="rec_scn_app",
    )
    scenario_name = st.text_input("Scenario name", key="rec_scn_name")
    start_url = st.text_input(
        "Recording start URL",
        value=next(a.base_url_pattern for a in apps if a.id == app_id),
        key="rec_scn_url",
    )
    if st.button("Create scenario", key="rec_scn_create") and scenario_name and start_url:
        sc = Scenario(
            id="sc-" + uuid.uuid4().hex[:8],
            name=scenario_name,
            kind="recorded",
            base_url="",
            steps=[],
            dataset=[],
            expected_outcome="success",
            application_id=app_id,
            recordings=[{"id": "placeholder", "start_url": start_url}],  # validation requires non-empty
            ai_test_cases=[],
        )
        save_scenario("data/scenarios", sc)
        st.session_state["last_created_scenario_id"] = sc.id
        st.success(f"Scenario created. Open it to start recording.")
        st.rerun()
```

The placeholder recording is replaced by a real one in Step 14.3 the first time the user records against this scenario.

- [ ] **Step 14.3: Add the recording flow to scenario detail**

In `ui/scenarios/detail.py`, add a top-level branch for `kind="recorded"` scenarios. Use the same pattern as the Recordings page — spawn `core.recorder_cli` as a subprocess, poll for output. Add this code at the top of the function that renders the scenario detail (after loading the scenario):

```python
import json, os, subprocess, sys, uuid
from datetime import datetime, timezone
from pathlib import Path
from core.applications import load_application
from core.auth_session import load_storage_state, is_storage_state_valid
from core.recording import load_recording

if scenario.kind == "recorded":
    app = load_application("data/applications", scenario.application_id)
    if not is_storage_state_valid(app):
        st.error("This application's login session is expired or missing. Refresh it on the Recordings page first.")
        return
    state = load_storage_state("data/storage_states", scenario.application_id)
    state_path = os.path.join("data/recorder_work", f"{scenario.id}_state_in.json")
    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    Path(state_path).write_text(json.dumps(state), encoding="utf-8")

    st.subheader("Recordings")
    real_recs = [r for r in scenario.recordings if r.get("id") and r["id"] != "placeholder"]
    for r in real_recs:
        st.write(f"• **{r.get('name', r['id'])}** ({len(r.get('steps', []))} steps)")
    if not real_recs:
        st.info("No recordings yet. Start one below.")

    rec_out = os.path.join("data/recorder_work", f"{scenario.id}_rec.yaml")
    cand_out = os.path.join("data/recorder_work", f"{scenario.id}_cand.json")
    proc_key = f"rec_proc_{scenario.id}"

    if proc_key not in st.session_state:
        start_url = st.text_input("Start URL", value=app.base_url_pattern, key=f"surl_{scenario.id}")
        name = st.text_input("Recording name", value="Happy path", key=f"rname_{scenario.id}")
        if st.button("Start recording", key=f"rstart_{scenario.id}") and start_url and name:
            for p in (rec_out, cand_out):
                if os.path.exists(p):
                    os.remove(p)
            proc = subprocess.Popen([
                sys.executable, "-m", "core.recorder_cli",
                "--app-id", scenario.application_id,
                "--start-url", start_url,
                "--output-recording", rec_out,
                "--output-candidates", cand_out,
                "--storage-state-path", state_path,
                "--name", name,
                "--headless", "false",
            ])
            st.session_state[proc_key] = proc.pid
            st.rerun()
    else:
        if not os.path.exists(rec_out):
            st.warning("Recording in progress. Close the browser window when done, then click Refresh.")
            if st.button("Refresh", key=f"rref_{scenario.id}"):
                st.rerun()
        else:
            new_rec = load_recording(rec_out)
            # Replace placeholder if present, else append.
            cleaned = [r for r in scenario.recordings if r.get("id") != "placeholder"]
            cleaned.append(new_rec.to_dict())
            scenario.recordings = cleaned
            save_scenario("data/scenarios", scenario)
            st.session_state.pop(proc_key, None)
            st.success(f"Recorded {len(new_rec.steps)} steps.")
            st.rerun()
```

This branch returns early (`return`) for `recorded` scenarios so the existing single-page / multi-page rendering doesn't run. Make sure the surrounding function is structured to allow that early return; if not, wrap the existing rendering in an `else` block.

- [ ] **Step 14.4: Manual smoke test**

1. Go to Scenarios → New Scenario → kind = "recorded" → pick the application created in Task 13 → create the scenario.
2. Open the scenario. Click Start recording. A Chromium opens at the start URL **with the saved storageState loaded** (you should be already logged in for any real app — for the sample form, no login needed).
3. Fill in a couple of fields, then close the browser window.
4. Click Refresh in Streamlit. The scenario's Recordings list should now show your recording with the correct step count.
5. Reload the Streamlit page. The recording should persist.

- [ ] **Step 14.5: Commit**

```bash
git add ui/scenarios/
git commit -m "feat(ui): record-a-scenario entry + scenario detail recording flow"
```

---

## Task 15: End-to-end integration test

A test that records against `test_form/sample_form.html`, persists, reloads, replays, and verifies the form ends up filled.

**Files:**
- Create: `tests/test_recording_e2e.py`

- [ ] **Step 15.1: Write the test**

In `tests/test_recording_e2e.py`:
```python
import os
import pytest
import yaml
from core.recorder import RecorderSession
from core.recording import save_recording, load_recording
from core.replay import replay_recording


@pytest.fixture
def sample_form_url():
    return "file://" + os.path.abspath("test_form/sample_form.html").replace("\\", "/")


@pytest.mark.asyncio
async def test_record_then_replay_roundtrip(sample_form_url, tmp_path):
    # ---- record ---------------------------------------------------
    session = RecorderSession(application_id="app-e2e", headless=True)
    await session.start(start_url=sample_form_url)
    # Drive the form (in real use the user does this in the headed browser).
    first_input_name = await session.page.evaluate(
        "() => document.querySelector('input').getAttribute('name')"
    )
    if not first_input_name:
        pytest.skip("sample form first input lacks a name")
    await session.page.fill(f"[name='{first_input_name}']", "E2E-VALUE")
    await session.page.evaluate(
        f"() => document.querySelector(\"[name='{first_input_name}']\").dispatchEvent(new Event('change', {{bubbles:true}}))"
    )
    await session.page.wait_for_timeout(100)
    recording = await session.stop(name="e2e")
    assert any(s.action == "fill" and s.value == "E2E-VALUE" for s in recording.steps)

    # ---- persist + reload -----------------------------------------
    path = tmp_path / "rec.yaml"
    save_recording(str(path), recording)
    reloaded = load_recording(str(path))
    assert reloaded == recording

    # ---- replay ---------------------------------------------------
    outcome = await replay_recording(reloaded, headless=True)
    assert outcome.failed_step_index is None, f"replay failed: {outcome.error}"
    assert outcome.completed_steps == len(recording.steps)
```

- [ ] **Step 15.2: Run the test**

Run: `pytest tests/test_recording_e2e.py -v`
Expected: PASS.

- [ ] **Step 15.3: Run the full test suite to check for regressions**

Run: `pytest tests/ -x --ignore=tests/test_ai_matcher.py --ignore=tests/test_ai_matcher_recipe.py --ignore=tests/test_ai_service.py --ignore=tests/test_ai_test_data.py`

(AI-dependent tests need Ollama running; skip in this sweep.)

Expected: all non-AI tests pass.

- [ ] **Step 15.4: Commit**

```bash
git add tests/test_recording_e2e.py
git commit -m "test(recording): end-to-end record-persist-reload-replay roundtrip"
```

---

## Demoable milestone — done

At this point you can demo:

1. Open Streamlit → **Recordings** page → create an application against any reachable target → record login (you handle CAPTCHA / 2FA manually) → pick success-signal elements → confirm.
2. Open **Scenarios** → create a new `recorded` scenario for that application → start recording → drive the happy path in the headed browser → stop recording.
3. Open the scenario, click Replay → headed browser opens with `storageState` loaded → walks every step → reports outcome.

What's missing (deferred to follow-on plans):
- **Healing** — locator drift currently fails the run instead of recovering.
- **AI test multiplication** — one recording is one test case.
- **DOM diff capture** — dynamic / inter-dependent fields aren't tracked.
- **Server response capture** — failure-mode AI grounding.
- **Polished UI** — minimal Streamlit, not the final tabbed layout from the spec.

---

## Future plans (reference, not part of this plan)

- `2026-05-15-scenario-recording-plan-2-healing.md` — wire `core/healer.py` to operate on fingerprints, write back on heal.
- `2026-05-15-scenario-recording-plan-3-ai-multiplication.md` — `generate_test_cases_from_recording()` + Test Cases tab.
- `2026-05-15-scenario-recording-plan-4-dom-diff.md` — `MutationObserver` + `revealed_elements`/`hidden_elements` capture and replay-time waits.
- `2026-05-15-scenario-recording-plan-5-server-response.md` — CDP network capture + failure-mode grounding in AI.
