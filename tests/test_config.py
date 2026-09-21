def test_config_exists():
    from xli.core.config import get_config
    c = get_config()
    assert c is not None

def test_default_provider():
    from xli.core.config import get_config
    c = get_config()
    provider = c.get_default_provider()
    assert provider in ["mistral", "openai", "anthropic", "openrouter"]
