# SYLPHY — Windows installation and operation

This release is a source package. It has not been installed or run on the target Windows machine during this handoff.

## 1. Prerequisites

- Windows 10/11
- Python 3.10 or newer (Python 3.10.11 was the project environment used during development)
- Ollama for Windows, running locally
- At least one local Ollama model installed

Verify Python and Ollama in PowerShell:

```powershell
py -3.10 --version
ollama --version
ollama list
```

## 2. Extract and install

Extract the ZIP to a folder you own, for example `D:\AI_Workspace\SYLPHY`. Open PowerShell in that folder:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

If PowerShell blocks activation for the current user, run this once in that PowerShell window:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Alternatively, skip activation and use `.\.venv\Scripts\python.exe` for commands.

## 3. Configure the local model

Open `config.toml`. Under `[ollama]`, set `host` and `model` to the local Ollama endpoint and a model shown by `ollama list`. Example:

```toml
[ollama]
host = "http://127.0.0.1:11434"
model = "YOUR_INSTALLED_MODEL:TAG"
```

Do not copy the placeholder literally. The model must already be installed in Ollama. You can pull one manually:

```powershell
ollama pull MODEL:TAG
```

The configured model is not guaranteed to be the best companion model; test latency, response quality, and context usage on your hardware.

## 4. Identity and optional features

- `identity.toml` contains the configurable SYLPHY identity. Keep the `[identity]` table and its required fields.
- `config.toml` controls memory, cognition, presence, avatar, exploration, vision, and voice.
- For the first launch, if optional hardware or model assets are not installed, consider disabling optional features by setting their `enabled = false` in the corresponding section: `[avatar]`, `[vision]`, `[voice]`, and `[exploration]`.
- Vision requires an installed Ollama vision-capable model and the optional Python extra: `python -m pip install -e ".[vision]"`. The camera is only accessed when an explicit `/vision look ...` command is used.
- Voice requires the optional stack: `python -m pip install -e ".[voice]"`. Voice model assets download or must be supplied locally according to the configuration. Voice listening starts only after `/voice start`.
- Avatar requires a configured image asset. The configured `assets/avatar/sylphy.png` is not included in this source package; provide the image or disable `[avatar]` until it is available.

## 5. Start SYLPHY

Make sure Ollama is running and the configured model is installed, then:

```powershell
python -m sylphy.app
```

Or after editable installation:

```powershell
sylphy
```

The terminal is the primary interface. At the session prompt:

- `n` — create a session
- `l` — list and resume a saved session
- `q` — quit session selection

Inside a conversation, enter ordinary text to chat. Use `/help` to see supported commands. Useful commands include:

- `/status`, `/runtime` — runtime status
- `/memory help` — confirmed memory management
- `/cognition status`, `/cognition list` — review memory candidates
- `/presence status` — presence queue
- `/avatar status|show|hide` — avatar controls
- `/vision status|devices|look <question>` — explicit camera/vision interaction
- `/voice status|devices|test|start|stop` — voice controls
- `/exploration status|list` — exploration status
- `/investigation help`, `/web help` — research commands
- `/quit` — return to session selection

## 6. Data and backups

Application data is stored in the configured data directory (default `data\`), including SQLite sessions, messages, memories, and task state. Logs are stored in the configured log directory (default `logs\`). Before upgrading, stop SYLPHY and back up the entire `data\` directory, especially `data\sylphy.sqlite3`.

Do not delete the database to troubleshoot without first making a backup; it contains conversation and memory data.

## 7. Tests

Run unit tests:

```powershell
python -m pytest -q
```

Run the live Ollama integration test separately (requires Ollama running and a configured test model):

```powershell
$env:SYLPHY_TEST_MODEL = "MODEL:TAG"
python -m pytest -q -m integration
```

The integration test uses a temporary database. It is not included in the default test run.

## 8. Troubleshooting

- **Ollama connection error:** verify `ollama list`, confirm Ollama is running, and check `[ollama].host`.
- **Model not found:** run `ollama pull MODEL:TAG`, then update `config.toml`.
- **Identity configuration error:** verify `identity.toml` contains a valid `[identity]` table and required values.
- **Optional feature errors:** disable the corresponding feature in `config.toml` until its dependency, model, or asset is installed.
- **Slow generation:** benchmark installed models using `python -m sylphy.benchmark --models MODEL:TAG --contexts 256 2048 8192`.

## Verification boundary

The handoff test result was produced in the development container with a temporary import shim for the `ollama` package. The shim only allowed unit-test collection and did not provide inference. Live Ollama inference, Windows camera drivers, microphone/speaker devices, avatar asset loading, and installation on the target machine remain unverified.
