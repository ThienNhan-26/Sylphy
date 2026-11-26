import sys
sys.path.insert(0, r'd:\CWDI\SYLPHY_CWDI\Sylphy-main\src')
import pytest
pytest.main(['tests/entity_conversation/test_identity_integration.py', '-v', '--tb=short'])
