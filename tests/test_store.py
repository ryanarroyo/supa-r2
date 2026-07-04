import pytest

from r2db import FilesystemTarget, Store


@pytest.fixture
def db(tmp_path):
    return Store(FilesystemTarget(tmp_path))


def test_put_get_roundtrip(db):
    users = db.collection("users")
    stored = users.put("alice", {"name": "Alice", "plan": "pro"})
    assert stored["name"] == "Alice"
    assert stored["id"] == "alice"
    assert "created_at" in stored and "updated_at" in stored

    got = users.get("alice")
    assert got["name"] == "Alice"
    assert got["plan"] == "pro"


def test_get_missing_returns_none(db):
    assert db.collection("users").get("nobody") is None
    assert db.collection("users").exists("nobody") is False


def test_replace_preserves_created_at_via_merge(db):
    users = db.collection("users")
    first = users.put("alice", {"name": "Alice"})
    created = first["created_at"]
    merged = users.update("alice", {"plan": "enterprise"})
    assert merged["created_at"] == created      # preserved
    assert merged["name"] == "Alice"            # untouched field kept
    assert merged["plan"] == "enterprise"       # new field applied


def test_full_replace_without_merge_drops_old_fields(db):
    users = db.collection("users")
    users.put("alice", {"name": "Alice", "plan": "pro"})
    users.put("alice", {"name": "Alice"})       # no merge -> replace
    assert "plan" not in users.get("alice")


def test_ids_and_count_and_all(db):
    users = db.collection("users")
    users.put("a", {"plan": "pro"})
    users.put("b", {"plan": "free"})
    users.put("c", {"plan": "pro"})
    assert users.ids() == ["a", "b", "c"]
    assert users.count() == 3
    pros = sorted(u["id"] for u in users.all(where=lambda u: u["plan"] == "pro"))
    assert pros == ["a", "c"]


def test_find_first_match(db):
    users = db.collection("users")
    users.put("a", {"plan": "free"})
    users.put("b", {"plan": "pro"})
    hit = users.find(lambda u: u["plan"] == "pro")
    assert hit["id"] == "b"
    assert users.find(lambda u: u["plan"] == "nope") is None


def test_delete_reports_existence(db):
    users = db.collection("users")
    users.put("a", {"x": 1})
    assert users.delete("a") is True
    assert users.delete("a") is False
    assert users.get("a") is None


def test_collections_listing(db):
    db.collection("users").put("a", {"x": 1})
    db.collection("orders").put("o1", {"total": 9})
    assert db.collections() == ["orders", "users"]


def test_prefix_isolates_namespaces(tmp_path):
    a = Store(FilesystemTarget(tmp_path), prefix="tenant-a")
    b = Store(FilesystemTarget(tmp_path), prefix="tenant-b")
    a.collection("users").put("x", {"who": "a"})
    b.collection("users").put("x", {"who": "b"})
    assert a.collection("users").get("x")["who"] == "a"
    assert b.collection("users").get("x")["who"] == "b"


@pytest.mark.parametrize("bad", ["a/b", "../etc", "", "has space"])
def test_rejects_unsafe_ids(db, bad):
    with pytest.raises(ValueError):
        db.collection("users").put(bad, {"x": 1})


def test_put_requires_dict(db):
    with pytest.raises(TypeError):
        db.collection("users").put("a", [1, 2, 3])


def test_stamp_times_can_be_disabled(tmp_path):
    db = Store(FilesystemTarget(tmp_path), stamp_times=False)
    stored = db.collection("k").put("only", {"v": 1})
    assert stored == {"v": 1}
