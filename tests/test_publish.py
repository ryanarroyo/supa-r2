import json

from r2db import Document, FilesystemTarget, publish


def _manifest(tmp_path, prefix):
    return json.loads((tmp_path / prefix / "manifest.json").read_text())


def test_publish_writes_docs_and_manifest(tmp_path):
    target = FilesystemTarget(tmp_path)
    docs = [
        Document("users/active.json", [{"id": "a"}, {"id": "b"}]),
        Document("stats/summary.json", {"count": 2}),
    ]
    res = publish(target, docs, prefix="v1")

    assert set(res.uploaded) == {"users/active.json", "stats/summary.json"}
    assert (tmp_path / "v1/users/active.json").exists()
    manifest = _manifest(tmp_path, "v1")
    assert manifest["count"] == 2
    keys = {d["key"] for d in manifest["documents"]}
    assert keys == {"users/active.json", "stats/summary.json"}


def test_republish_skips_unchanged(tmp_path):
    target = FilesystemTarget(tmp_path)
    docs = [Document("a.json", {"v": 1})]
    publish(target, docs, prefix="v1")
    res = publish(target, docs, prefix="v1")     # identical second run
    assert res.skipped == ["a.json"]
    assert res.uploaded == []


def test_publish_prune_removes_stale(tmp_path):
    target = FilesystemTarget(tmp_path)
    publish(target, [Document("a.json", 1), Document("b.json", 2)], prefix="v1")
    res = publish(target, [Document("a.json", 1)], prefix="v1", prune=True)
    assert "v1/b.json" in res.pruned
    assert not (tmp_path / "v1/b.json").exists()
    assert (tmp_path / "v1/a.json").exists()


def test_manifest_extra_is_merged(tmp_path):
    target = FilesystemTarget(tmp_path)
    publish(target, [Document("a.json", 1)], prefix="v1",
            manifest_extra={"app": "demo"})
    assert _manifest(tmp_path, "v1")["app"] == "demo"
