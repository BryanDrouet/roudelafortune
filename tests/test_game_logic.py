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


def test_all_players_skip_does_not_award_points_and_keeps_current_player(client):
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


def test_delete_game_room_releases_player_usernames():
    game_code = "ROOM99"
    username = "alice"
    r.set(f"game:{game_code}", username, ex=3600)
    r.set(f"game:{game_code}:status", "waiting", ex=3600)
    r.lpush(f"game:{game_code}:players", username)
    r.set(f"user:{username}", username, ex=3600)

    app_module.delete_game_room(game_code)

    assert r.get(f"user:{username}") is None
    assert not r.exists(f"game:{game_code}:players")


def test_deleted_room_shows_room_deleted_message(client):
    game_code = "ROOM88"
    username = "alice"
    client.set_cookie("username", username, domain="localhost")
    client.set_cookie("game", game_code, domain="localhost")

    resp = client.get("/waiting", follow_redirects=True)

    assert resp.status_code == 200
    assert b"La salle" in resp.data
    assert game_code.encode() in resp.data
    assert b"supprim\xc3\xa9e." in resp.data


def test_setusername_rejects_username_already_claimed_by_someone_else(client):
    r.set("user:Bryan_Drouet2", "Bryan_Drouet2", ex=3600)

    resp = client.post("/setusername", data={"username": "Bryan_Drouet2"}, follow_redirects=True)

    assert resp.status_code == 200
    assert b"est d\xc3\xa9j\xc3\xa0 pris." in resp.data
    assert "username" not in resp.request.cookies


def test_pages_are_not_cached_so_cleared_cookies_show_no_pseudo(client):
    resp = client.get("/")

    assert resp.headers["Cache-Control"] == "no-store, no-cache, must-revalidate"
    assert b'value="player_' in resp.data


def test_new_visitors_get_a_unique_reserved_default_username(client):
    resp1 = client.get("/")
    assigned1 = resp1.headers.getlist("Set-Cookie")
    username1 = next(c.split("=", 1)[1].split(";", 1)[0] for c in assigned1 if c.startswith("username="))

    with app.test_client() as other_client:
        resp2 = other_client.get("/")
        assigned2 = resp2.headers.getlist("Set-Cookie")
        username2 = next(c.split("=", 1)[1].split(";", 1)[0] for c in assigned2 if c.startswith("username="))

    assert username1 != username2
    assert r.get(f"user:{username1}") == username1
    assert r.get(f"user:{username2}") == username2


def test_joining_game_silently_renews_username_reservation(client):
    client.set_cookie("username", "alice", domain="localhost")

    resp = client.post("/joingame", data={"game_code": "1234"}, follow_redirects=True)

    assert resp.status_code == 200
    assert b"Veuillez valider votre pseudo" not in resp.data
    assert r.get("user:alice") == "alice"


def test_admin_stays_first_in_players_board_after_others_join(client):
    r.set("user:admin", "admin", ex=3600)
    r.set("user:bob", "bob", ex=3600)
    r.set("user:carol", "carol", ex=3600)

    client.set_cookie("username", "admin", domain="localhost")
    client.post("/newgame")
    game_code = next(key.split(":")[1] for key in r.keys("game:*") if key.count(":") == 1 and r.get(key) == "admin")

    client.set_cookie("username", "bob", domain="localhost")
    client.post("/joingame", data={"game_code": game_code})

    client.set_cookie("username", "carol", domain="localhost")
    client.post("/joingame", data={"game_code": game_code})

    players = r.lrange(f"game:{game_code}:players", 0, -1)
    assert players == ["admin", "bob", "carol"]


def test_resolve_redis_host_uses_docker_internal_in_container(monkeypatch):
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.setattr(app_module.os.path, "exists", lambda path: path == "/.dockerenv")
    assert app_module.resolve_redis_host() == "host.docker.internal"


def test_build_redis_client_falls_back_to_host_docker_internal(monkeypatch):
    attempts = []

    class DummyRedis:
        def __init__(self, host, **kwargs):
            self.host = host
            attempts.append(host)

        def ping(self):
            if self.host == "redis":
                raise redis.exceptions.TimeoutError("Timeout connecting to server")
            if self.host == "host.docker.internal":
                return True
            return True

    monkeypatch.setattr(app_module.redis, "Redis", DummyRedis)
    monkeypatch.setattr(app_module, "resolve_redis_host", lambda: "redis")

    client = app_module.build_redis_client()

    assert client.host == "host.docker.internal"
    assert "redis" in attempts
    assert "host.docker.internal" in attempts


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
    r.set("user:alice", "alice", ex=3600)

    index_resp = client.get("/")
    assert b"Reprendre la partie" not in index_resp.data

    join_resp = client.post("/joingame", data={"game_code": "2380"}, follow_redirects=True)
    assert b"La partie" in join_resp.data
    assert b"2380" in join_resp.data
    assert b"est inconnue." in join_resp.data


def test_player_can_join_a_game_already_in_progress(client):
    game_code = "INPROG1"
    word = "casa"
    r.set(f"game:{game_code}", "alice", ex=3600)
    r.set(f"game:{game_code}:status", "playing", ex=3600)
    r.set(f"game:{game_code}:word", word, ex=3600)
    r.set(f"game:{game_code}:playerplay", 0, ex=3600)
    r.set(f"game:{game_code}:money", 100, ex=3600)
    r.set(f"game:{game_code}:nb_words", 2, ex=3600)
    r.rpush(f"game:{game_code}:players", "alice")
    r.rpush(f"game:{game_code}:{word}", *all_letters)
    r.expire(f"game:{game_code}:{word}", 3600)
    r.set("user:bob", "bob", ex=3600)

    client.set_cookie("username", "bob", domain="localhost")
    resp = client.post("/joingame", data={"game_code": game_code}, follow_redirects=True)

    assert resp.status_code == 200
    assert b"est inconnue." not in resp.data
    assert "bob" in r.lrange(f"game:{game_code}:players", 0, -1)



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


def test_winner_of_the_word_plays_first_on_the_next_round(client, monkeypatch):
    game_code = "WIN01"
    word = "casa"
    new_word = "moto"
    monkeypatch.setattr(app_module, "get_available_words", lambda exclude=None: new_word)

    r.set(f"game:{game_code}", "A", ex=3600)
    r.set(f"game:{game_code}:status", "playing", ex=3600)
    r.set(f"game:{game_code}:word", word, ex=3600)
    r.set(f"game:{game_code}:playerplay", 1, ex=3600)
    r.set(f"game:{game_code}:money", 100, ex=3600)
    r.set(f"game:{game_code}:nb_words", 2, ex=3600)
    r.rpush(f"game:{game_code}:players", "A", "B")
    r.expire(f"game:{game_code}:players", 3600)
    r.rpush(f"game:{game_code}:{word}", *all_letters)
    r.expire(f"game:{game_code}:{word}", 3600)

    # C'est le tour de B, qui devine le mot entier.
    client.set_cookie("username", "B", domain="localhost")
    resp = client.post("/guess", data={"game_code": game_code, "text": word})

    assert resp.status_code == 302
    assert r.get(f"game:{game_code}:word") == new_word
    assert r.get(f"game:{game_code}:playerplay") == "1"


def test_any_player_can_vote_skip_regardless_of_turn(client):
    game_code = "SKIP03"
    word = "casa"
    r.set(f"game:{game_code}", "A", ex=3600)
    r.set(f"game:{game_code}:status", "playing", ex=3600)
    r.set(f"game:{game_code}:word", word, ex=3600)
    r.set(f"game:{game_code}:playerplay", 0, ex=3600)
    r.lpush(f"game:{game_code}:players", "B", "A")
    r.expire(f"game:{game_code}:players", 3600)
    r.rpush(f"game:{game_code}:{word}", *all_letters)
    r.expire(f"game:{game_code}:{word}", 3600)

    # C'est le tour de A, mais B peut quand même voter pour passer immédiatement.
    client.set_cookie("username", "B", domain="localhost")
    resp = client.post("/guess", data={"game_code": game_code, "action": "skip"})

    assert resp.status_code == 302
    assert r.smembers(f"game:{game_code}:skip_votes") == {"B"}
    assert r.get(f"game:{game_code}:playerplay") == "0"


def test_generate_game_hash_does_not_crash_after_a_skip_vote(client):
    game_code = "SKIP04"
    word = "casa"
    r.set(f"game:{game_code}", "A", ex=3600)
    r.set(f"game:{game_code}:status", "playing", ex=3600)
    r.set(f"game:{game_code}:word", word, ex=3600)
    r.set(f"game:{game_code}:playerplay", 0, ex=3600)
    r.lpush(f"game:{game_code}:players", "B", "A")
    r.expire(f"game:{game_code}:players", 3600)
    r.rpush(f"game:{game_code}:{word}", *all_letters)
    r.expire(f"game:{game_code}:{word}", 3600)

    client.set_cookie("username", "A", domain="localhost")
    client.post("/guess", data={"game_code": game_code, "action": "skip"})

    # Générer un hash lisait 'skip_votes' comme une liste alors que c'est un set, ce qui plantait la synchronisation live.
    game_hash = app_module.generate_game_hash(game_code)
    assert isinstance(game_hash, str) and len(game_hash) == 8


def test_finished_page_shows_winner_and_restart_button_for_admin(client):
    game_code = "END01"
    r.set(f"game:{game_code}", "admin", ex=3600)
    r.set(f"game:{game_code}:status", "finished", ex=3600)
    r.set(f"game:{game_code}:word", "casa", ex=3600)
    r.rpush(f"game:{game_code}:players", "admin", "bob")
    r.set(f"game:{game_code}:score:admin", 500, ex=3600)
    r.set(f"game:{game_code}:score:bob", 900, ex=3600)

    client.set_cookie("username", "admin", domain="localhost")
    client.set_cookie("game", game_code, domain="localhost")

    resp = client.get("/game")

    assert resp.status_code == 200
    assert b"Relancer la partie" in resp.data
    assert b'<div class="player-name">bob</div>' in resp.data
    assert resp.data.index(b'<div class="player-name">bob</div>') < resp.data.index(b'<div class="player-name">admin</div>')


def test_restart_game_resets_state_and_returns_to_waiting(client):
    game_code = "END02"
    old_word = "casa"
    r.set(f"game:{game_code}", "admin", ex=3600)
    r.set(f"game:{game_code}:status", "finished", ex=3600)
    r.set(f"game:{game_code}:word", old_word, ex=3600)
    r.rpush(f"game:{game_code}:players", "admin", "bob")
    r.rpush(f"game:{game_code}:{old_word}", *all_letters)
    r.set(f"game:{game_code}:score:admin", 500, ex=3600)
    r.set(f"game:{game_code}:score:bob", 900, ex=3600)
    r.set(f"game:{game_code}:playerplay", 1, ex=3600)

    client.set_cookie("username", "admin", domain="localhost")
    resp = client.post("/restartgame", data={"game_code": game_code}, follow_redirects=True)

    assert resp.status_code == 200
    assert r.get(f"game:{game_code}:status") == "waiting"
    assert r.get(f"game:{game_code}:score:admin") is None
    assert r.get(f"game:{game_code}:score:bob") is None
    assert r.get(f"game:{game_code}:playerplay") is None
    assert r.lrange(f"game:{game_code}:players", 0, -1) == ["admin", "bob"]


def test_restart_game_rejects_non_admin(client):
    game_code = "END03"
    r.set(f"game:{game_code}", "admin", ex=3600)
    r.set(f"game:{game_code}:status", "finished", ex=3600)
    r.rpush(f"game:{game_code}:players", "admin", "bob")

    client.set_cookie("username", "bob", domain="localhost")
    resp = client.post("/restartgame", data={"game_code": game_code}, follow_redirects=True)

    assert resp.status_code == 200
    assert r.get(f"game:{game_code}:status") == "finished"
