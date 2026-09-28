import pytest
import redis

import app as app_module
from app import app, r, generate_game_hash, all_letters


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture(autouse=True)
def clean_redis():
    r.flushdb()


def test_hash_changes_when_letters_are_removed():
    game_code = "HASH42"
    word = "casa"
    r.set(f"game:{game_code}", "admin", ex=3600)
    r.set(f"game:{game_code}:status", "playing", ex=3600)
    r.set(f"game:{game_code}:word", word, ex=3600)
    r.set(f"game:{game_code}:playerplay", 0, ex=3600)
    r.lpush(f"game:{game_code}:players", "A", "B")
    r.expire(f"game:{game_code}:players", 3600)
    r.rpush(f"game:{game_code}:{word}", *all_letters)
    r.expire(f"game:{game_code}:{word}", 3600)

    old_hash = generate_game_hash(game_code)
    r.lrem(f"game:{game_code}:{word}", 0, "a")

    assert old_hash != generate_game_hash(game_code)


def test_all_players_skip_does_not_award_points_and_advances_turn(client):
    game_code = "SKIP01"
    word = "casa"
    r.set(f"game:{game_code}", "A", ex=3600)
    r.set(f"game:{game_code}:status", "playing", ex=3600)
    r.set(f"game:{game_code}:word", word, ex=3600)
    r.set(f"game:{game_code}:playerplay", 0, ex=3600)
    r.set(f"game:{game_code}:money", 100, ex=3600)
    r.set(f"game:{game_code}:nb_words", 2, ex=3600)
    r.lpush(f"game:{game_code}:players", "B", "A")
    r.expire(f"game:{game_code}:players", 3600)
    r.rpush(f"game:{game_code}:{word}", *all_letters)
    r.expire(f"game:{game_code}:{word}", 3600)

    client.set_cookie("username", "A", domain="localhost")
    resp = client.post("/guess", data={"game_code": game_code, "action": "skip"})
    assert resp.status_code == 302

    client.set_cookie("username", "B", domain="localhost")
    resp = client.post("/guess", data={"game_code": game_code, "action": "skip"})
    assert resp.status_code == 302

    assert r.get(f"game:{game_code}:score:A") is None
    assert r.get(f"game:{game_code}:score:B") is None
    assert r.get(f"game:{game_code}:playerplay") == "0"


def test_resolve_redis_host_strips_whitespace(monkeypatch):
    monkeypatch.setenv("REDIS_HOST", " redis ")
    assert app_module.resolve_redis_host() == "redis"


def test_same_username_is_allowed_for_current_user(client):
    username = "alice"
    r.set(f"user:{username}", username, ex=3600)
    client.set_cookie("username", username, domain="localhost")

    resp = client.post("/setusername", data={"username": username})

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_set_username_handles_redis_timeout_gracefully(client, monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise redis.exceptions.TimeoutError("Timeout connecting to server")

    monkeypatch.setattr(app_module.r, "get", raise_timeout)
    client.set_cookie("username", "old-user", domain="localhost")

    resp = client.post("/setusername", data={"username": "new-user"})

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_changing_username_releases_previous_name(client):
    old_username = "alice"
    new_username = "bob"
    r.set(f"user:{old_username}", old_username, ex=3600)
    client.set_cookie("username", old_username, domain="localhost")

    resp = client.post("/setusername", data={"username": new_username})

    assert resp.status_code == 302
    assert r.get(f"user:{old_username}") is None
    assert r.get(f"user:{new_username}") == new_username


def test_index_hides_stale_resume_and_unknown_game_flash_is_sentence(client):
    client.set_cookie("game", "2380", domain="localhost")
    client.set_cookie("username", "alice", domain="localhost")

    index_resp = client.get("/")
    assert b"Reprendre la partie" not in index_resp.data

    join_resp = client.post("/joingame", data={"game_code": "2380"}, follow_redirects=True)
    assert b"La partie" in join_resp.data
    assert b"2380" in join_resp.data
    assert b"est inconnue." in join_resp.data


def test_all_players_skip_reloads_word_in_redis(client, monkeypatch):
    game_code = "SKIP02"
    old_word = "casa"
    new_word = "moto"
    monkeypatch.setattr(app_module, "get_available_words", lambda exclude=None: new_word)

    r.set(f"game:{game_code}", "A", ex=3600)
    r.set(f"game:{game_code}:status", "playing", ex=3600)
    r.set(f"game:{game_code}:word", old_word, ex=3600)
    r.set(f"game:{game_code}:playerplay", 0, ex=3600)
    r.set(f"game:{game_code}:money", 100, ex=3600)
    r.set(f"game:{game_code}:nb_words", 2, ex=3600)
    r.lpush(f"game:{game_code}:players", "B", "A")
    r.expire(f"game:{game_code}:players", 3600)
    r.rpush(f"game:{game_code}:{old_word}", *all_letters)
    r.expire(f"game:{game_code}:{old_word}", 3600)

    client.set_cookie("username", "A", domain="localhost")
    resp = client.post("/guess", data={"game_code": game_code, "action": "skip"})
    assert resp.status_code == 302

    client.set_cookie("username", "B", domain="localhost")
    resp = client.post("/guess", data={"game_code": game_code, "action": "skip"})
    assert resp.status_code == 302

    assert r.get(f"game:{game_code}:word") == new_word
    assert r.get(f"game:{game_code}:skip_votes") is None
    assert not r.exists(f"game:{game_code}:{old_word}")
