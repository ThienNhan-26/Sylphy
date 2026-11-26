# SYLPHY S2-005-R2: Live Prompt & Identity Semantics Audit

## A. Full Identity Field Mapping

| Field | Loaded? | In Runtime? | In System Prompt? | Notes |
|-------|---------|-------------|-------------------|-------|
| name | Yes | Yes | **NO** | Omitted |
| identity | Yes | Yes | **NO** | "A local-first AI companion... SYLPHY is under development" — omitted |
| personality | Yes | Yes | **YES** | Only identity field included in prompt |
| communication_style | Yes | Yes | **NO** | Omitted |
| relationship | Yes | Yes | **NO** | Omitted |
| boundaries | Yes | Yes | **NO** | Omitted |
| version | Yes | Yes | **NO** | "1.0" — never communicated |

**Code location**: `src/sylphy_entity/interaction/conversation_runtime.py:107-129`
Only `self._identity.personality` is used; other fields stored but omitted from model request.

**Loading chain verified**: daemon.py → EntitySupervisor → load_identity() → ConversationRuntime._identity → _respond() personality injection

## B. Live Request Capture

**Limitation**: Entity daemon not running in this environment. Could not capture actual JSON payload sent to http://127.0.0.1:9931/v1/chat/completions. Analysis based on code inspection of prompt construction.

## C. Model Behavior Isolation

System prompt structure (when identity loaded):
```
[personality from identity.toml] + " " + [hardcoded system_norms] + "\n" + [recalled memory]
```

System prompt (when identity NOT loaded):
```
"You are Sylphy, a local AI companion with a calm, warm, personal voice. " + [system_norms] + "\n" + [recalled memory]
```

Direct-model comparison not performed (no live daemon capture).

## D. Identity Semantics Analysis

The `identity` field contains: "SYLPHY is under development and does not claim consciousness, hidden activity, or capabilities the application does not have."

**Hypotheses**:
1. Model may describe itself as generic LM or unfinished product
2. Model may prioritize product-status disclaimers over companion identity
3. Model may misunderstand distinction between software development and conversational presence

**Semantic distinction** (per Owner guidance):
- Companion identity: personality, communication_style, relationship, boundaries
- Software development status: "under development" disclaimer
- Actual capabilities/limitations: boundaries field

Currently, the "under development" phrase is never sent to the model.

## Root Causes (Ranked)

1. **Only personality included in system prompt** (High confidence) — `conversation_runtime.py:108-112` uses only `self._identity.personality`; 6 other fields stored but omitted
2. **identity description not mapped to prompt** (High confidence) — "under development" disclaimer not communicated to model
3. **version not communicated** (Medium) — "1.0" loaded but never in prompt
4. **communication_style not utilized** (Medium) — speaking style not injected
5. **relationship and boundaries not modeled** (Medium) — constraint fields not in model context

## Remaining Unknowns

- Live request/response evidence (daemon not running)
- Model behavioral difference with full vs truncated prompt
- Owner's intent on current mapping
- Semantic effect of "under development" phrase
- Which fields should be in the prompt

## Recommended Next Action (Owner Approval Required)

**Option A**: Accept current behavior (only personality in prompt)
**Option B**: Map all identity fields to system prompt (requires prompt modifications)
**Option C**: Explicitly model "under development" disclaimer in prompt
**Option D**: Distinguish companion identity from development status explicitly

**No implementation authorized** — Owner must review and decide.