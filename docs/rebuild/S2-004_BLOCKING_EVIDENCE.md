# S2-004 — Blocking Evidence Clarification: daemon.py load_config

**Audit Date:** 2026-11-25
**Governing authority:** SYLPHY_MASTER_PLAN_FUTURE_HANDOVER_v3.0.md
**Mode:** Read-only evidence investigation

---

## 1. Complete Import Section of daemon.py

### `src/sylphy_entity/daemon.py` (lines 1-20)

```python
"""Command-line entry point for the independent SYLPHY Entity Daemon."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sylphy_entity.runtime.instance_lock import InstanceAlreadyRunning
from sylphy_entity.runtime.paths import default_runtime_dir
from sylphy_entity.runtime.supervisor import EntitySupervisor
```

**No import of `load_config` or `sylphy.config` exists in daemon.py.**

---

## 2. Every Definition and Import of `load_config` in the Repository

### `src/sylphy/config.py:83`

```python
def load_config(path: Path | None = None) -> AppConfig:
    """Load TOML config; SYLPHY_CONFIG and OLLAMA_* env vars may override it."""
    config_path = path or Path(os.environ.get("SYLPHY_CONFIG", "config.toml"))
    config_path = config_path.resolve()
    with config_path.open("rb") as file:
        raw = tomllib.load(file)
    app = raw.get("sylphy", {})
    ollama = raw.get("ollama", {})
    ...
```

**Only one definition exists:** `src/sylphy/config.py:83`.

**No other `def load_config` exists anywhere in the repository.**

---

## 3. How `load_config` Becomes Available in daemon.py's Namespace

**IT DOES NOT.**

- `daemon.py` line 30: `config = load_config()`
- There is **no** `from sylphy.config import load_config` or equivalent import
- There is **no** `import sylphy` or `from sylphy import` that would bring `load_config` into scope
- Python will raise `NameError: name 'load_config' is not defined` at runtime

**RUNTIME-VERIFIED:** The NameError is confirmed by executing the daemon entrypoint.

```bash
python3 -c "
import sys
sys.path.insert(0, 'D:\\\\CWDI\\\\SYLPHY_CWDI\\\\Sylphy-main\\\\src')
from sylphy_entity.daemon import main
try:
    main()
except NameError as e:
    print(f'NameError: {e}')
"
# Output: NameError: name 'load_config' is not defined
```

---

## 3b. The Blocking Defect

**The symbol `load_config` is NOT made available in any way.**

- There is **no import** of `load_config` in `daemon.py`
- There is **no** `from sylphy.config import load_config`
- There is **no** `import sylphy` that would bring `load_config` into scope
- Python's namespace lookup will fail with `NameError`

**The production daemon startup path is BLOCKED by this defect.**

---

## 4. Legacy Boundary: daemon.py and `src/sylphy/`

** daemon.py imports only from `sylphy_entity.*` packages — confirmed.**

No import of `src/sylphy/` (the legacy package) exists in `daemon.py`.

**TEST-VERIFIED:** The package-boundary test passes.

**However:** The `load_config` defect is a more fundamental blocker than the legacy boundary.

---

## 5. Identity Authority Chain Status

**THE END-TO-END IDENTITY CHAIN IS BROKEN.**

Because `daemon.py` cannot execute past `config = load_config()` (NameError), the following chain never executes:

```
config.toml → load_config() → config.identity_file → EntitySupervisor → load_identity() → ConversationRuntime → _respond()
```

**UNVERIFIED:** The identity integration cannot be verified through the production daemon path because the daemon fails before reaching identity loading.

**RISK:** Any S2-001/S2-003 changes that relied on the daemon startup path are **invalidated** until the `load_config` import is fixed.

---

## 6. Final Conclusion

### CONFIRMED:
- `load_config` is defined in `src/sylphy/config.py:83`
- `daemon.py` calls `load_config()` on line 30 without importing it
- The `sylphy-entity` entrypoint (`sylphy_entity.daemon:main`) is the production entrypoint
- The package boundary (no `sylphy` import from `sylphy_entity/`) is confirmed

### FAILED:
- `load_config` is not imported in `daemon.py`, causing `NameError` at runtime
- The production daemon startup path is **blocked** by this defect
- The end-to-end identity chain from S2-001/S2-003 cannot be verified through the daemon

**VERDICT: UNVERIFIED** — The `load_config` blocking defect must be resolved before any acceptance decision can be made for S2-004.

### Required Fix:
Add `from sylphy.config import load_config` to `src/sylphy_entity/daemon.py` (line ~13, with the other imports) so that `load_config()` can execute successfully.

**The owner must resolve this defect before S2-004 can be accepted.**"""