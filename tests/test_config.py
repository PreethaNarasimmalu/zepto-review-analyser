from zepto_discovery import config


def test_play_store_target():
    assert config.PLAY_STORE_APP_ID == "com.zeptoconsumerapp"
    assert config.PLAY_STORE_LOCALE == "en_IN"


def test_time_window_and_sampling():
    assert config.TIME_WINDOW_DAYS == 90
    assert config.TIMEFRAME_OPTIONS_DAYS == (7, 30, 90)
    assert config.SAMPLE_SIZE_CAP == 1200
    assert config.RECENCY_CHUNKS == 3
    assert config.THIN_WINDOW_MIN_REVIEWS > 0


def test_data_dirs_are_nested_under_repo_data_dir():
    for d in (
        config.RAW_DIR,
        config.FILTERED_DIR,
        config.SAMPLED_DIR,
        config.TAGGED_DIR,
        config.CLUSTERED_DIR,
        config.SYNTHESIS_DIR,
    ):
        assert d.parent == config.DATA_DIR


def test_repo_structure_exists():
    assert config.REPO_ROOT.joinpath("app").is_dir()
    assert config.REPO_ROOT.joinpath("tests").is_dir()
    assert config.REPO_ROOT.joinpath("zepto_discovery").is_dir()
    for d in (
        config.RAW_DIR,
        config.FILTERED_DIR,
        config.SAMPLED_DIR,
        config.TAGGED_DIR,
        config.CLUSTERED_DIR,
        config.SYNTHESIS_DIR,
    ):
        assert d.is_dir()


def test_gitignore_excludes_pipeline_data():
    gitignore = config.REPO_ROOT.joinpath(".gitignore").read_text()
    assert "data/raw/*" in gitignore
    assert "secrets.toml" in gitignore
