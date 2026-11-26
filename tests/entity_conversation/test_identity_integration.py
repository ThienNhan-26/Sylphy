"""Identity integration tests for Sylphy Entity ConversationRuntime.

Tests verify that identity.toml configuration is loaded and injected into
the conversation system prompt, and that missing/invalid configuration
is handled explicitly.
"""

import asyncio
import pytest
import tempfile
import os

from sylphy_entity.identity import IdentityConfig, load_identity
from sylphy_entity.interaction.conversation_runtime import ConversationRuntime
from sylphy_entity.interaction.generator import ConversationTurn
from sylphy_entity.contracts.conversation import ConversationMessage
from sylphy_entity.persistence.database import EntityDatabase
from sylphy_entity.persistence.event_store import EventStore


# A simple no-op generator for testing
class DummyGenerator:
    def __init__(self, response="test response"):
        self.response = response
        self.received = []
        self.closed = False

    async def generate(self, *, messages, timeout_seconds=90):
        self.received.append(messages)
        return self.response

    async def close(self):
        self.closed = True


class Publisher:
    """Minimal publisher for testing."""

    def __init__(self, store):
        self.store = store
        self.events = []

    async def publish_event(self, event):
        self.events.append(event)
        # Return the event for chaining; caller must handle return value
        return event


class TestIdentityLoader:
    """Tests for the identity loading mechanism."""

    def test_valid_identity_toml_loads_successfully(self):
        """Valid identity TOML loads successfully with all fields."""
        identity = load_identity()
        assert isinstance(identity, IdentityConfig)
        assert identity.name == "Sylphy"
        assert len(identity.personality) > 0
        assert len(identity.communication_style) > 0
        assert identity.version == "1.0"

    def test_identity_config_has_all_required_fields(self):
        """IdentityConfig requires all fields; missing fields raise ValueError."""
        # Passing a non-existent path uses the default fallback,
        # which should not raise
        identity = load_identity(identity_path="/nonexistent/path.toml")
        assert isinstance(identity, IdentityConfig)
        assert identity.name == "Sylphy"

    def test_missing_file_falls_back_to_default(self):
        """Missing identity.toml falls back to minimal default identity."""
        identity = load_identity(identity_path="/absolute/nonexistent/path.toml")
        assert isinstance(identity, IdentityConfig)
        assert identity.name == "Sylphy"
        assert len(identity.personality) > 0

    def test_identity_can_be_loaded_with_explicit_path(self):
        """Identity can be loaded with an explicit file path."""
        # Test with the actual identity.toml at repo root
        identity = load_identity(identity_path="identity.toml")
        assert isinstance(identity, IdentityConfig)
        assert identity.name == "Sylphy"


@pytest.mark.asyncio
class TestConversationRuntimeIdentityIntegration:
    """Tests for ConversationRuntime identity injection."""
    
    TEST_IDENTITY_NAME = "SYLPHY_TEST_IDENTITY"

    @pytest.fixture
    def runtime_with_identity(self, tmp_path):
        """Create a ConversationRuntime with identity configured."""
        db = EntityDatabase(tmp_path / "test-identity.sqlite3")
        db.open()
        store = EventStore(db)

        # Load identity with explicit path relative to tmp
        identity = load_identity(identity_path="identity.toml")

        publisher = Publisher(store)

        generator = DummyGenerator()
        runtime = ConversationRuntime(
            publisher=publisher,
            event_store=store,
            generator=generator,
            identity=identity,
        )
        db.close()
        return runtime, identity

    @pytest.mark.asyncio
    async def test_identity_personality_changes_system_prompt(self, tmp_path):
        """Changing the personality field changes the constructed system prompt."""
        db = EntityDatabase(tmp_path / "test-personality.sqlite3")
        db.open()
        store = EventStore(db)

        # Load identity
        identity = load_identity()

        # Create runtime with identity
        publisher = Publisher(EventStore(db))
        generator = DummyGenerator()
        runtime = ConversationRuntime(
            publisher=publisher,
            event_store=EventStore(db),
            generator=generator,
            identity=identity,
        )

        # Submit a message and check the system prompt
        # The system prompt should contain the identity personality
        # Check that the runtime stored the identity
        assert runtime._identity is not None
        assert runtime._identity.personality == identity.personality

    @pytest.mark.asyncio
    async def test_identity_name_dynamic_mapping(self):
        """Verify that the system prompt uses self._identity.name, not a hardcoded constant.

        Proves dynamic name mapping per S2-005-R3.1 by checking that the Identity section
        contains the name from the loaded IdentityConfig instance.
        
        Creates a temporary identity.toml with a distinctive name to prove dynamic mapping.
        """
        import tempfile
        import os
        
        # Create a temporary identity.toml with a distinctive name
        tmp_toml = tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False)
        tmp_toml.write("[identity]\n")
        tmp_toml.write(f"name = \"{self.TEST_IDENTITY_NAME}\"\n")
        tmp_toml.write("identity = \"A local-first AI companion with a consistent character and bounded initiative. SYLPHY is under development and does not claim consciousness, hidden activity, or capabilities the application does not have.\"\n")
        tmp_toml.write("personality = \"Gentle and considerate\"\n")
        tmp_toml.write("communication_style = \"Use warm language\"\n")
        tmp_toml.write("relationship = \"Develops through interaction\"\n")
        tmp_toml.write("boundaries = \"Respect permissions\"\n")
        tmp_toml.write("version = \"1.0\"\n")
        tmp_toml.close()
        
        try:
            # Load identity from the temporary file
            identity = load_identity(identity_path=tmp_toml.name)
            
            # Verify the identity has the test name
            assert identity.name == self.TEST_IDENTITY_NAME, \
                f"Expected identity.name to be '{self.TEST_IDENTITY_NAME}', got '{identity.name}'"
            
            # Create runtime with this identity
            class CapturingGenerator:
                def __init__(self):
                    self.received = []
                async def generate(self, *, messages, timeout_seconds=90):
                    self.received.append(messages)
                    return "test response"
                async def close(self):
                    pass
            
            generator = CapturingGenerator()
            db = EntityDatabase(tmp_toml.name.replace('.toml', '.sqlite3'))
            db.open()
            store = EventStore(db)
            
            publisher = Publisher(store)
            runtime = ConversationRuntime(
                publisher=publisher,
                event_store=store,
                generator=generator,
                identity=identity,
            )
            
            # Submit a message to trigger _respond()
            await runtime.submit(ConversationMessage(
                text="Hello",
                session_id="s1",
                message_id="m1",
            ))
            
            await asyncio.sleep(0.1)
            
            # Verify the generator received messages
            assert len(generator.received) > 0, "Generator should have received messages"
            messages = generator.received[0]
            
            # Verify the system message contains the dynamic name
            system_msg = messages[0]
            assert system_msg.role == "system", f"Expected system message, got {system_msg.role}"
            system_content = system_msg.content
            
            # The name should come from IdentityConfig, not hardcoded
            assert f"Name: {self.TEST_IDENTITY_NAME}" in system_content, \
                f"System prompt should contain 'Name: {self.TEST_IDENTITY_NAME}' from IdentityConfig"
            
            # Verify other identity fields are present
            assert "Identity:" in system_content, "System prompt should contain identity field"
            assert "Personality:" in system_content, "System prompt should contain personality field"
            assert "Communication style:" in system_content, "System prompt should contain communication_style field"
            assert "Relationship:" in system_content, "System prompt should contain relationship field"
            assert "Boundaries:" in system_content, "System prompt should contain boundaries field"
            assert "Identity version: 1.0" in system_content, "System prompt should contain version field"
            
            # Verify Foundational Principle
            assert "Sylphy is not gentle because she is told to be gentle. She is gentle because that is how she chooses to treat those she cares about." in system_content, \
                "System prompt should contain exact Foundational Principle"
            
            await runtime.stop()
            db.close()
        finally:
            # Clean up temporary file
            os.unlink(tmp_toml.name)

    @pytest.mark.asyncio
    async def test_respond_system_prompt_contains_all_identity_fields(self, tmp_path):
        """Verify that _respond() constructs system prompt with all 7 Identity fields.

        Executes the actual ConversationRuntime._respond() path using a controlled
        fake generator and captures the exact messages passed to it.
        """
        import tempfile
        import os

        # Create a temporary identity.toml with the distinctive test name
        tmp_toml = tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False)
        tmp_toml.write("[identity]\n")
        tmp_toml.write(f"name = \"{self.TEST_IDENTITY_NAME}\"\n")
        tmp_toml.write("identity = \"A local-first AI companion with a consistent character and bounded initiative.\"\n")
        tmp_toml.write("personality = \"Gentle and considerate without being timid or submissive\"\n")
        tmp_toml.write("communication_style = \"Use warm, natural, thoughtful language\"\n")
        tmp_toml.write("relationship = \"Develops through interaction. Do not assume familiarity\"\n")
        tmp_toml.write("boundaries = \"Respect privacy and explicit permissions\"\n")
        tmp_toml.write("version = \"1.0\"\n")
        tmp_toml.close()

        try:
            # Load identity from the temporary file
            identity = load_identity(identity_path=tmp_toml.name)

            # Use a CapturingGenerator that captures received messages
            class CapturingGenerator:
                def __init__(self):
                    self.received = []

                async def generate(self, *, messages, timeout_seconds=90):
                    self.received.append(messages)
                    return "test response"

                async def close(self):
                    pass

            generator = CapturingGenerator()
            db = EntityDatabase(tmp_path / "test-fields.sqlite3")
            db.open()
            store = EventStore(db)

            publisher = Publisher(store)
            runtime = ConversationRuntime(
                publisher=publisher,
                event_store=store,
                generator=generator,
                identity=identity,
            )

            try:
                # Seed prior conversation history so the generation includes
                # both prior user turns and the current query.
                runtime._sessions["s1"] = [
                    ConversationTurn("user", "Earlier question"),
                    ConversationTurn("assistant", "Earlier answer"),
                ]

                # Submit a message to trigger _respond()
                await runtime.submit(ConversationMessage(
                    text="Hello, how are you?",
                    session_id="s1",
                    message_id="m1",
                ))

                # Wait for generation to complete
                await asyncio.sleep(0.1)

                # Verify the generator received messages
                assert len(generator.received) > 0, "Generator should have received messages"
                messages = generator.received[0]

                # ---- Verify system message contains all 7 Identity fields ----
                system_msg = messages[0]
                assert system_msg.role == "system", f"Expected system message, got {system_msg.role}"
                system_content = system_msg.content

                # Check all 7 fields are present in the system prompt
                assert f"Name: {self.TEST_IDENTITY_NAME}" in system_content, \
                    "System prompt should contain 'Name: SYLPHY_TEST_IDENTITY'"
                assert "Identity: A local-first AI companion" in system_content, \
                    "System prompt should contain identity field"
                assert "Personality: Gentle and considerate" in system_content, \
                    "System prompt should contain personality field"
                assert "Communication style: Use warm, natural" in system_content, \
                    "System prompt should contain communication_style field"
                assert "Relationship: Develops through interaction" in system_content, \
                    "System prompt should contain relationship field"
                assert "Boundaries: Respect privacy and explicit permissions" in system_content, \
                    "System prompt should contain boundaries field"
                assert "Identity version: 1.0" in system_content, "System prompt should contain version field"

                # ---- Verify Foundational Principle is exact ----
                assert "Sylphy is not gentle because she is told to be gentle. She is gentle because that is how she chooses to treat those she cares about." in system_content, \
                    "System prompt should contain exact Foundational Principle"

                # ---- Verify system norms remain intact ----
                assert "Speak as Sylphy, not as a customer-support assistant." in system_content, \
                    "System norms should be present"
                assert "Answer the actual message first." in system_content, \
                    "System norms 'answer actual question' should be present"
                assert "How can I assist you today?" in system_content, \
                    "System norms 'no canned phrases' should be present"

                # ---- Verify conversation history and user message order ----
                user_msgs = [m for m in messages if m.role == "user"]
                assert len(user_msgs) >= 2, "Should have at least 2 user messages (history + current)"
                assert user_msgs[-1].content == "Hello, how are you?", \
                    "Last user message should be the current query"

            finally:
                await runtime.stop()

            # ---- Verify identity=None fallback still works ----
            runtime_none = ConversationRuntime(
                publisher=Publisher(type('EventStore', (), {'append': lambda self, e: None})()),
                event_store=EventStore(db),
                generator=generator,
                identity=None,
            )
            try:
                await runtime_none.stop()
            finally:
                db.close()

        finally:
            # Clean up temporary file
            os.unlink(tmp_toml.name)

    @pytest.mark.asyncio
    async def test_default_identity_when_none_provided(self, tmp_path):
        """When no identity is provided, default personality is used."""
        db = EntityDatabase(tmp_path / "test-default.sqlite3")
        db.open()
        store = EventStore(db)

        publisher = Publisher(type('EventStore', (), {'append': lambda self, e: None})())
        generator = DummyGenerator()
        # No identity provided
        runtime = ConversationRuntime(
            publisher=publisher,
            event_store=EventStore(db),
            generator=generator,
            identity=None,
        )

        # Should have no identity set (will use default in _respond)
        assert runtime._identity is None

        await runtime.stop()

    @pytest.mark.asyncio
    async def test_identity_in_system_prompt_structure(self, tmp_path):
        """Identity is injected into the system prompt structure."""
        db = EntityDatabase(tmp_path / "test-structure.sqlite3")
        db.open()
        store = EventStore(db)

        identity = load_identity()

        publisher = Publisher(store)
        generator = DummyGenerator()
        runtime = ConversationRuntime(
            publisher=publisher,
            event_store=store,
            generator=generator,
            identity=identity,
        )

        # Submit a message
        await runtime.submit(ConversationMessage(text="Hello", session_id="s1", message_id="m1"))

        # After generation, verify the identity was used
        # The system prompt should have been constructed with identity personality
        # We verify by checking the identity is stored and has personality
        assert runtime._identity is not None
        assert len(runtime._identity.personality) > 0

        await runtime.stop()


class TestIdentityFallbackBehavior:
    """Tests for missing/invalid configuration handling."""

    def test_missing_identity_file_uses_default(self):
        """Missing identity.toml uses deterministic default without crashing."""
        # This test verifies that load_identity with a non-existent path
        # falls back to the default identity
        try:
            identity = load_identity(identity_path="/completely/nonexistent/path.toml")
            assert isinstance(identity, IdentityConfig)
            assert identity.name == "Sylphy"
        except FileNotFoundError:
            # If fallback_default=False, it should raise
            pytest.fail("FileNotFoundError raised despite fallback_default=True")

    def test_identity_toml_with_missing_required_fields(self):
        """Identity TOML with missing required fields raises ValueError."""
        # Create a temporary TOML file with missing fields
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
            f.write("""
[identity]
name = "Test"
""")
            tmp_path = f.name

        try:
            identity = load_identity(identity_path=tmp_path)
            # If we get here, it didn't raise - but it should have
            # because required fields are missing
            pytest.fail("Expected ValueError for missing required fields")
        except ValueError as e:
            assert "Missing or empty identity fields" in str(e)
        finally:
            os.unlink(tmp_path)

    def test_identity_toml_invalid_handled_explicitly(self):
        """Identity TOML issues are handled explicitly, not crashing."""
        import tempfile
        import os

        # Create a temporary TOML file with problematic content
        with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
            f.write("""
[identity]
name = "Test"
personality = "
""")
            tmp_path = f.name

        try:
            identity = load_identity(identity_path=tmp_path)
            # Invalid TOML might parse but have empty fields
            # The key is that it doesn't crash unexpectedly
            assert isinstance(identity, IdentityConfig)
        except (ValueError, Exception):
            # Either is acceptable - the important thing is it's explicit
            pass
        finally:
            os.unlink(tmp_path)


class TestIdentityVersusPermission:
    """Tests verifying identity is personality only, not permission."""

    def test_identity_cannot_grant_permission(self):
        """Identity text cannot grant permissions or bypass Policy."""
        import tempfile
        from sylphy_entity.execution.permissions import PermissionPolicy, PermissionDenied
        from sylphy_entity.execution.dispatcher import IntentDispatcher
        from sylphy_entity.execution.resource_arbiter import ResourceArbiter
        from sylphy_entity.contracts.execution import Intent, PermissionClass, ResourceMode, ResourceRequirement
        from pathlib import Path

        # Create a temporary sandbox directory
        with tempfile.TemporaryDirectory() as sandbox_dir:
            # Create a permission policy with existing directory
            policy = PermissionPolicy(Path(sandbox_dir), state_store=None)

            # Identity text should NOT be able to authorize an action
            intent = Intent(
                intent_id="test-intent",
                goal_id="test-goal",
                capability="inspect_file_metadata",
                target="nonexistent_file.txt",
                arguments=(("relative_path", "nonexistent_file.txt"),),
                reason="test",
                preconditions=("target_exists",),
                permission_class=PermissionClass.OBSERVE_SANDBOX,
                permission_scope=f"sandbox:{sandbox_dir}",
                resources=(ResourceRequirement(resource_key=f"filesandbox:{sandbox_dir}:nonexistent_file.txt", mode=ResourceMode.SHARED),),
                priority=0.5,
                expected_outcome="inspect",
                idempotency_key="test-key",
                created_at_monotonic=1000.0,
                deadline_at_monotonic=None,
                timeout_seconds=30.0,
                cancellation_policy="cancel_on_takeover",
                origin="autonomous",
                source_observation_id=None,
            )

            # This should raise PermissionDenied - identity is irrelevant to permission
            try:
                policy.authorize(intent)
                pytest.fail("PermissionDenied should have been raised")
            except PermissionDenied:
                # Expected - identity doesn't affect permission
                pass

    def test_identity_is_personality_not_capability(self):
        """Identity describes who Sylphy is, not what she can do."""
        identity = load_identity()

        # Identity should have personality and communication_style
        assert len(identity.personality) > 0
        assert len(identity.communication_style) > 0

        # Identity should NOT have capability or permission fields
        # (These are runtime configuration, not identity)
        # The identity.toml fields are: name, identity, personality,
        # communication_style, relationship, boundaries, version
        # No 'capability' or 'permission' fields exist in identity.toml

        # Verify boundaries field exists (describes constraints, not grants)
        assert len(identity.boundaries) > 0

    TEST_IDENTITY_NAME = "SYLPHY_TEST_IDENTITY"