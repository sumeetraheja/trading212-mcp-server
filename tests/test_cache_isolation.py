from utils.client import Trading212Client


def test_distinct_cache_dirs_produce_distinct_storage(tmp_path):
    a = Trading212Client(
        api_key="kA", api_secret="sA", environment="demo",
        cache_dir=str(tmp_path / "A"),
    )
    b = Trading212Client(
        api_key="kB", api_secret="sB", environment="demo",
        cache_dir=str(tmp_path / "B"),
    )
    # Storage instances must be distinct objects.
    assert a.client._storage is not b.client._storage
    # Each directory must exist and be distinct.
    assert (tmp_path / "A").is_dir()
    assert (tmp_path / "B").is_dir()


def test_no_cache_dir_reuses_default_storage():
    from utils.hishel_config import default_storage as default
    c = Trading212Client(api_key="k", api_secret="s", environment="demo")
    assert c.client._storage is default
