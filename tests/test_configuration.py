def test_new_configuration_wins_and_legacy_fallback(monkeypatch):
    from agent.env_utils import get_env
    monkeypatch.setenv('CODEPILOT_LOAD_ENV', 'false')
    monkeypatch.delenv('CODEPILOT_LOG_LEVEL', raising=False)
    monkeypatch.setenv('LX_AICODING_LOG_LEVEL', 'WARNING')
    assert get_env('CODEPILOT_LOG_LEVEL', 'INFO') == 'WARNING'
    monkeypatch.setenv('CODEPILOT_LOG_LEVEL', 'DEBUG')
    assert get_env('CODEPILOT_LOG_LEVEL', 'INFO') == 'DEBUG'
