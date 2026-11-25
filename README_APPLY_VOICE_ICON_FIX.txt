SYLPHY R7 — Voice Icon / Emoji Sanitization Patch

Scope
- Replaces src/sylphy_entity/voice/text.py
- Replaces tests/entity_voice/test_speech_text.py
- Does not include or modify avatar/, Electron, node_modules/, package files, or Desktop assets.

What changes
- Keeps the original UI/display reply unchanged.
- Removes emoji sequences, flags, keycaps, common decorative symbols/icons, and emoji shortcodes from the TTS-only text.
- Preserves ordinary words and sentence punctuation.
- Includes regression tests for flower/sparkle/heart/star/fire/arrow/play/speaker icons, keycaps, and flags.

Apply
1. Stop only the SYLPHY Entity daemon with Ctrl+C in its daemon PowerShell window.
2. Extract this ZIP into the project root:
   D:\CWDI\SYLPHY_Sprint_10_plus_1_fixed\sylphy_work
   Allow overwrite of the two listed files only.
3. From the project root run:
   py -3.13 -m pytest -q tests\entity_voice\test_speech_text.py
4. Restart the Entity daemon with the same arguments as before.
5. Test a reply containing emoji/icons. The UI should still show them; Kokoro should not speak them.

IMPORTANT
- Do NOT run npm ci.
- Do NOT delete or replace avatar\node_modules.
- Do NOT reinstall Electron.
- This patch has not yet been validated against the live Windows/Kokoro process; the included 20 unit tests passed in the patch workspace.
