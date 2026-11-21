"""Prompt and context construction contracts (not model behavior tests)."""

from datetime import datetime, timezone

import pytest
from sylphy.context import ContextBuilder, CharacterTokenEstimator
from sylphy.core import Message
from sylphy.memory import Memory


class StaticRetriever:
    def __init__(self, memories):
        self.memories = memories

    def retrieve(self, query, limit):
        return self.memories[:limit]

# Helper function to safely load a prompt from the context builder for testing
def get_system_prompt(system_instructions: str, identity_instructions: str) -> str:
    """Helper to reconstruct the merged system prompt for assertion."""
    builder = ContextBuilder(system_instructions, identity_instructions, 10000, CharacterTokenEstimator()).build([], "")
    return builder[0]["content"]

# --- Identity Tests ---

def test_identity_prompt_is_merged_not_listed():
    """Verify that the system prompt is a single, merged block, not a mechanical list."""
    system_inst = "You are an AI assistant. Be helpful."
    identity_inst = "Name: SYLPHY. Personality: Gentle and considerate. Communication style: Use warm, natural, thoughtful language."
    
    merged_prompt = get_system_prompt(system_inst, identity_inst)
    
    # Assert the structure reflects the merged logic, not a field dump
    assert "Configured identity:" in merged_prompt
    assert "Name: SYLPHY" in merged_prompt
    assert "Personality: Gentle and considerate" in merged_prompt
    assert "Behavioural guidelines:" in merged_prompt
    assert "Speak as SYLPHY, but do not introduce yourself unless the user asks who you are or what you are." in merged_prompt
    assert "Answer the user's actual question directly; do not default to generic assistance offers." in merged_prompt

def test_identity_and_system_instructions_share_one_system_message():
    system_inst = "Be a helpful assistant."
    identity_inst = "Name: SYLPHY. Personality: Gentle and considerate."

    context = ContextBuilder(system_inst, identity_inst, 1000).build([], "Test query")

    system_messages = [message for message in context if message["role"] == "system"]
    assert len(system_messages) == 1
    assert system_messages[0]["content"].index(system_inst) < system_messages[0]["content"].index(identity_inst)
    assert context[-1] == {"role": "user", "content": "Test query"}

# --- Conversation Tests ---

def test_system_prompt_directs_direct_answering():
    """Verify the system prompt contains the directive to answer the actual question."""
    system_inst = "You are a helpful assistant."
    identity_inst = "Name: SYLPHY. Personality: Gentle and considerate."
    
    merged_prompt = get_system_prompt(system_inst, identity_inst)
    
    assert "Answer the user's actual question directly; do not default to generic assistance offers." in merged_prompt

def test_no_repetition_in_behavioral_invariants():
    """Verify that behavioral invariants regarding repetition are present."""
    system_inst = "Be a helpful assistant."
    identity_inst = "Name: SYLPHY. Personality: Gentle and considerate."
    
    merged_prompt = get_system_prompt(system_inst, identity_inst)
    
    # Check for negative constraints on repetition
    assert "Avoid repeating yourself or using the same closing phrases." in merged_prompt

# --- Context Construction Tests ---

def test_context_assembly_ordering_is_deterministic():
    """Verify exact order: merged prompt, retrieved memory, chronological history, current input."""
    now = datetime.now(timezone.utc)
    memory = Memory("m1", "Prefers concise replies", "preference", now, now)
    history = [
        Message("u1", "s1", "user", "What time is it?", now),
        Message("a1", "s1", "assistant", "It is 10:00.", now),
    ]
    context = ContextBuilder(
        "System instructions.", "Identity instructions.", 1000,
        CharacterTokenEstimator(), memory_retriever=StaticRetriever([memory]),
        memory_budget=200, memory_limit=2,
    ).build(history, "How about the weather?")

    assert [message["role"] for message in context] == ["system", "system", "user", "assistant", "user"]
    assert "System instructions." in context[0]["content"]
    assert "Identity instructions." in context[0]["content"]
    assert "Prefers concise replies" in context[1]["content"]
    assert [message["content"] for message in context[2:]] == [
        "What time is it?", "It is 10:00.", "How about the weather?"
    ]

def test_context_truncation_preserves_system_instructions():
    """Oversized history is dropped; required prompt and current user input remain intact."""
    now = datetime.now(timezone.utc)
    history = [
        Message("old", "s1", "user", "old " * 1000, now),
        Message("recent", "s1", "assistant", "recent reply", now),
    ]
    context = ContextBuilder("System instructions.", "Identity instructions.", 250).build(history, "current query")

    assert "System instructions." in context[0]["content"]
    assert "Identity instructions." in context[0]["content"]
    assert all(message["content"] != "old " * 1000 for message in context)
    assert any(message["content"] == "recent reply" for message in context)
    assert context[-1] == {"role": "user", "content": "current query"}


def test_merged_prompt_includes_language_matching_instruction():
    prompt = get_system_prompt("System instructions.", "Name: SYLPHY.")
    assert "Match the language of your reply to the user's most recent message." in prompt
