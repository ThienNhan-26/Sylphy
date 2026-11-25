# Soul Source Provenance

## 1. Source identity

| Field | Value |
|---|---|
| Original source path verified in Turn 1 | `D:\AI\soul.txt` |
| Source path used for repository incorporation | `D:\CWDI\soul.txt` |
| Repository copy | `docs/identity/source/soul.txt` |
| Date incorporated | 2026-11-22 |
| Size | 26,711 bytes |
| SHA-256 of incorporated copy | `3B10D19F4F39360FBFDAF07AC525DAC0E8A60ECC7871AC28EFAE295BEF3AB7F9` |
| Original last-write timestamp | 2026-11-21 12:02:46 |
| Copy method | Byte-for-byte filesystem copy; no text editing, normalization, summarization, or reformatting |

### Availability note

Turn 1 verified and read `D:\AI\soul.txt`. At the start of Turn 2, that path was no longer present. A file with the same size (26,711 bytes) and the same last-write timestamp (2026-11-21 12:02:46) was found at `D:\CWDI\soul.txt`. That file was copied unchanged to the repository.

The Turn 1 audit did not record a SHA-256 hash of `D:\AI\soul.txt` before it disappeared. Therefore byte identity between the Turn 1 path and the incorporated copy is **inferred from matching size and timestamp, not cryptographically proven against the original path**. The hash above is the authoritative hash of the incorporated copy.

## 2. Provenance classification

| Question | Answer |
|---|---|
| Is this original user material? | It was supplied by the user as source material. The repository did not author it. |
| Is it a software specification? | No. It contains no requirement IDs, acceptance criteria, API contracts, or testable engineering rules. |
| Is it an Animama transcript? | Yes. The opening is a transcript of an Animama YouTube presentation by Raymond. |
| Does it contain other material? | Yes. It also contains Luna clip transcripts and a Vietnamese character analysis of Sylphy and Rudeus. |
| Is it normative for SYLPHY behavior? | No. It is inspirational/reference material. The operational identity authority remains `identity.toml`. |

## 3. Source material versus operational requirements

### Source material in `soul.txt`

- Animama product walkthrough: avatar, AI, memory, emotions, behaviors, and effects.
- Memory design discussion: virtual memory files, identity files, profile, daily notes/episodic memory, memory manager, versioning, and approval of changes.
- Emotion design discussion: Plutchik-inspired opposing emotions, GoEmotions labels, emotional tone and intensity driving face, posture, voice, and reactions.
- Behavior design discussion: gestures, reactions, idle gestures, and Anima Pose Clips.
- Visual effects discussion: avatar, scene, and particle effects.
- Explicit honesty statement: *"The Animas are not real. You are talking to an AI, and it probably doesn't love you."*
- A statement that the app is not a replacement for real human connection.
- Luna clip transcripts used as illustrative companion interaction.
- A Vietnamese analysis of the fictional character Sylphy: gentleness, patience, care, emotional healing, and relational tolerance.

### Operational requirements in the repository

Operational identity requirements live in:

- `identity.toml` — the current operational identity authority.
- `src/sylphy/identity.py` — required-field validation and prompt rendering.
- `src/sylphy/context.py` — identity/system-prompt assembly.
- `src/sylphy/interaction_policy.py` — deterministic interaction gates.
- `config.toml` — feature enablement and bounds.

`soul.txt` does not override these files. Where the source and the operational identity differ, `identity.toml` governs runtime behavior.

## 4. Explicit non-goals and cautions

1. **The Animama transcript is inspirational/reference material, not a specification that SYLPHY must reproduce.** SYLPHY is not required to implement Animama's VRM avatar, 24-emotion model, Anima Pose Clips, memory manager, or creator tools.
2. **The Sylphy character analysis is inspiration, not permission to impersonate a fictional character or reproduce copyrighted character dialogue.** SYLPHY must not claim to be Sylphy from *Mushoku Tensei*, reproduce that character's lines, or present copyrighted character material as its own identity.
3. **The source's warmth and patience themes do not authorize fabricated intimacy, dependency, or claims of feeling.** The source itself says the Animas are not real and that the app is not a replacement for human connection.
4. **No source statement is treated as a testable requirement unless it is also represented in the Soul Contract with an observable boundary.** The contract distinguishes source-derived inspiration from existing architecture and new engineering invariants.

## 5. Relationship to `identity.toml`

```
soul.txt                         provenance and inspiration only
    |
    v
docs/identity/soul_contract.md   derived principles, boundaries, and verification labels
    |
    v
identity.toml                    operational identity authority loaded at startup
    |
    v
context.py / runtime / policy    prompt assembly and deterministic enforcement
    |
    v
tests                            deterministic contract verification only
```

`identity.toml` remains the single operational identity authority. The Soul Contract does not create a second identity configuration and does not replace `identity.toml`. Writing a contract does not make the model obey it; model behavior remains **PROMPT-DEFINED / BEHAVIOR-NOT-VERIFIED** unless a deterministic application mechanism enforces the boundary.
