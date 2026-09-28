from flask import Flask, Response, redirect, render_template, make_response, request, url_for, flash
import random
import redis
import os
import pandas
import hashlib
import json
import time


#env
voyelles = ["a", "e", "i", "o", "u", "y"]
consonnes = ["b", "c", "d", "f", "g", "h", "j", "k", "l", "m", "n", "p", "q", "r", "s", "t", "v", "w", "x", "z"]
all_letters = voyelles + consonnes
print("All letters:", all_letters)
port=int(os.getenv("PORT") or 8000)

# Connexion à Redis
def resolve_redis_host():
    env_host = os.getenv("SERVICE_NAME_REDIS") or os.getenv("REDIS_HOST")
    if env_host:
        return env_host.strip()
    if os.path.exists("/.dockerenv"):
        return "host.docker.internal"
    return "localhost"


# Essaie l'hôte configuré puis une liste de secours, pour tolérer les environnements
# Docker où le nom de service "redis" ne se résout pas toujours (ex. sandbox de dev).
def build_redis_client():
    configured_host = resolve_redis_host()
    host_candidates = []

    if configured_host:
        host_candidates.append(configured_host)

    for fallback in ("redis", "host.docker.internal", "localhost", "127.0.0.1"):
        if fallback not in host_candidates:
            host_candidates.append(fallback)

    for host in dict.fromkeys(host_candidates):
        client = redis.Redis(
            host=host,
            port=int(os.getenv("REDIS_PORT", 6379)),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        try:
            client.ping()
            print(f"Connected to Redis on {host}")
            return client
        except redis.exceptions.RedisError as exc:
            print(f"Redis unavailable on {host}: {exc}")

    return redis.Redis(
        host=configured_host,
        port=int(os.getenv("REDIS_PORT", 6379)),
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


r = build_redis_client()

# Fonction pour générer un hash unique de l'état de la partie
def generate_game_hash(game_code):
    word = r.get(f"game:{game_code}:word")
    data = {
        "status": r.get(f"game:{game_code}:status"),
        "word": word,
        "playerplay": r.get(f"game:{game_code}:playerplay"),
        "players": r.lrange(f"game:{game_code}:players", 0, -1),
        "money": r.get(f"game:{game_code}:money"),
        "nb_words": r.get(f"game:{game_code}:nb_words"),
        "letters": r.lrange(f"game:{game_code}:{word}", 0, -1) if word else [],
        "last_event": r.get(f"game:{game_code}:last_event"),
        "skip_votes": sorted(r.smembers(f"game:{game_code}:skip_votes"))
    }
    data_str = json.dumps(data, sort_keys=True)
    return hashlib.sha256(data_str.encode()).hexdigest()[:8]

app = Flask(__name__)

random.seed()
secure_random = random.SystemRandom()
key = random.randrange(1111111111, 9999999999, 1)
app.secret_key = os.getenv("SECRET_KEY", f"secret_key_{key}")


@app.after_request
def add_security_headers(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none';"
    )
    # Empêche le navigateur de réafficher une page contenant un ancien pseudo après un clear des cookies.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


@app.errorhandler(redis.exceptions.RedisError)
def handle_redis_error(error):
    print(f"Redis error: {error}")
    flash("Le serveur Redis est indisponible. Réessayez plus tard.")
    return redirect(url_for("index"))

#pandas connection test
try:
    pd = pandas.read_csv("data/data.csv")
    pd = pd["data"]

    print("Pandas connection successful")

except Exception as e:
    print(f"Error connecting to pandas: {e}")
    pd = None


# On prépare un nouveau mot pour le tour suivant et on remet les lettres à zéro.
def load_new_round_word(game_code, previous_word=None):
    next_word = get_available_words(exclude=previous_word)

    if previous_word:
        r.delete(f"game:{game_code}:{previous_word}")

    r.set(f"game:{game_code}:word", next_word, ex=3600)
    r.delete(f"game:{game_code}:{next_word}")
    r.rpush(f"game:{game_code}:{next_word}", *all_letters)
    r.expire(f"game:{game_code}:{next_word}", 3600)
    return next_word


def get_available_words(exclude=None):
    try:
        words = pandas.read_csv("data/data.csv")["data"].dropna().astype(str).str.strip()
        words = list(dict.fromkeys(word for word in words if word))
        if exclude is not None:
            words = [word for word in words if word != exclude]
        if not words:
            return "defaultword"
        print(f"Successfully read {len(words)} distinct words from CSV.")
        return secure_random.choice(words)
    except Exception as e:
        print(f"Error reading words from CSV: {e}")
        return "defaultword"  # Fallback word list


def release_username(username):
    if not username:
        return
    r.delete(f"user:{username}")


def reserve_default_username(max_attempts=5):
    # Chaque tentative est une réservation atomique pour garantir l'unicité du pseudo par défaut.
    for _ in range(max_attempts):
        candidate = f"player_{secure_random.randint(10000, 99999)}"
        if r.set(f"user:{candidate}", candidate, nx=True, ex=3600):
            return candidate
    return None


def delete_game_room(game_code):
    if not game_code:
        return

    players = r.lrange(f"game:{game_code}:players", 0, -1)
    for player in players:
        release_username(player)

    room_keys = [key for key in r.keys(f"game:{game_code}*") if key]
    for key in room_keys:
        r.delete(key)


## User logique
@app.route("/")
def index():
    username = request.cookies.get("username")
    game_code = request.cookies.get("game")
    newly_assigned = None

    try:
        if not username:
            newly_assigned = reserve_default_username()
            username = newly_assigned or username

        stale_game = bool(game_code) and not r.exists(f"game:{game_code}:status") and not r.exists(f"game:{game_code}")
    except redis.exceptions.RedisError:
        return render_template("index.html", code=username, default_username="", game_code=None)

    if stale_game:
        flash(f"La salle '{game_code}' a été supprimée.")
        resp = make_response(render_template("index.html", code=username, default_username="", game_code=None))
        resp.set_cookie("game", "", expires=0, secure=request.is_secure, httponly=True)
    else:
        resp = make_response(render_template("index.html", code=username, default_username="", game_code=game_code if game_code else None))

    if newly_assigned:
        resp.set_cookie("username", newly_assigned, max_age=3600, secure=request.is_secure, httponly=True)

    return resp

@app.route("/setusername", methods=["GET", "POST"])
@app.route("/setusername/", methods=["GET", "POST"])
def set_username():
    current_username = request.cookies.get("username")

    if request.method == "POST":
        username = request.form.get("username")
    else:
        username = request.args.get("username")
        if not username:
            return redirect(url_for("index"))

    if username is None:
        flash("Le pseudo est obligatoire.")
        return redirect(url_for("index"))

    username = username.strip()

    if not username:
        flash("Le pseudo est obligatoire.")
        return redirect(url_for("index"))

    try:
        if len(username) < 3 or len(username) > 20:
            flash("Le nom d'utilisateur doit contenir entre 3 et 20 caractères.")
            return redirect(url_for("index"))

        if username == current_username:
            r.set(f"user:{username}", username, ex=3600)
        else:
            # Réservation atomique pour éviter que deux clients ne prennent le même pseudo en même temps.
            claimed = r.set(f"user:{username}", username, nx=True, ex=3600)
            if not claimed:
                flash(f"Le pseudo '{username}' est déjà pris.")
                return redirect(url_for("index"))

            if current_username and r.get(f"user:{current_username}") == current_username:
                r.delete(f"user:{current_username}")
    except redis.exceptions.RedisError as exc:
        print(f"Redis error while updating username: {exc}")
        flash("Le serveur Redis est indisponible. Réessayez plus tard.")
        return redirect(url_for("index"))

    resp = redirect(url_for("index"))
    resp.set_cookie('username', username, max_age=3600, secure=request.is_secure, httponly=True)
    return resp

## join game logique
@app.route("/newgame", methods=["POST"])
def newgame():
    username = request.cookies.get("username")

    if not username:
        return redirect(url_for("index"))

    # Le pseudo est déjà attribué par défaut, on renouvelle juste sa réservation.
    r.set(f"user:{username}", username, ex=3600)

    if request.cookies.get("game"):
        return redirect(url_for("game"))

    code = str(random.randrange(1111, 9999))

    word = str(get_available_words())  # Récupère un mot aléatoire depuis le CSV

    # Une partie par code, associée à son utilisateur.
    r.set(f"game:{code}", username, ex=3600)
    # On enregistre le mot courant et on crée la liste des lettres encore jouables.
    r.set(f"game:{code}:word", word, ex=3600)
    r.rpush(f"game:{code}:{word}", *all_letters)
    r.expire(f"game:{code}:{word}", 3600)
    # Créer la liste des joueurs pour cette partie
    r.rpush(f"game:{code}:players", username)
    r.expire(f"game:{code}:players", 3600)
    # initialiser le statut de la partie
    r.set(f"game:{code}:status", "waiting", ex=3600)
    r.set(f"game:{code}:money", random.randrange(50, 1000, 50), ex=3600)
    r.set(f"game:{code}:nb_words", 5, ex=3600)

    # Rediriger vers la page de jeu avec le code de la partie dans les cookies

    resp = redirect(url_for("game"))
    resp.set_cookie(
        "game",
        code,
        max_age=3600,
        secure=request.is_secure,
        httponly=True
    )
    return resp

@app.route("/joingame", methods=["POST"])
def joingame(game_code=None):
    username = request.cookies.get("username")

    if not username:
        return redirect(url_for("index"))

    # Le pseudo est déjà attribué par défaut, on renouvelle juste sa réservation.
    r.set(f"user:{username}", username, ex=3600)

    if not game_code:
        game_code = request.form.get("game_code")

    game_status = r.get(f"game:{game_code}:status")
    game_exsit = game_status in ("waiting", "playing") and r.get(f"game:{game_code}") is not None

    if game_exsit:
        resp = redirect(url_for("game"))
        resp.set_cookie(
            "game",
            game_code,
            max_age=3600,
            secure=request.is_secure,
            httponly=True
        )
        if username not in r.lrange(f"game:{game_code}:players", 0, -1):
            r.rpush(f"game:{game_code}:players", username)
        return resp

    flash(f"La partie '{game_code}' est inconnue.")
    return redirect(url_for("index"))

## game logique
@app.route("/waiting", methods=["GET", "POST"])
def waiting():
    game_code = request.cookies.get("game")
    username = request.cookies.get("username")

    if not game_code or not username:
        flash("Partie ou utilisateur introuvable.")
        return redirect(url_for("index"))

    if not r.exists(f"game:{game_code}:status") and not r.exists(f"game:{game_code}"):
        flash(f"La salle '{game_code}' a été supprimée.")
        resp = redirect(url_for("index"))
        resp.set_cookie("game", "", expires=0, secure=request.is_secure, httponly=True)
        return resp

    if r.get(f"game:{game_code}:status") == "playing":
        return redirect(url_for("game"))

    if request.form.get("switch_status"):
        if r.get(f"game:{game_code}") == username:
            if r.get(f"game:{game_code}:status") == "waiting":
                r.set(f"game:{game_code}:status", "playing", ex=3600)
                return redirect(url_for("game"))
            else:
                flash(f"Impossible de changer le statut de la partie {game_code}.")
                return redirect(url_for("waiting"))
        else:
            flash(f"Vous n'êtes pas l'administrateur de la partie {game_code}.")
            return redirect(url_for("waiting"))

    if request.form.get("nb_words"):
            if r.get(f"game:{game_code}") == username:
                nb_words = request.form.get("nb_words")
                if nb_words and nb_words.isdigit() and 1 <= int(nb_words) <= 10:
                    r.set(f"game:{game_code}:nb_words", int(nb_words), 3600)
                    return redirect(url_for("game"))
                else:
                    flash(f"Impossible de changer le nombre de mots de la partie {game_code}. Le nombre maximum de manches est de 10.")
                    return redirect(url_for("waiting"))
            else:
                flash(f"Vous n'êtes pas l'administrateur de la partie {game_code}.")
                return redirect(url_for("waiting"))

    if r.get(f"game:{game_code}:status") == "waiting" and username in r.lrange(f"game:{game_code}:players", 0, -1):
        players = r.lrange(f"game:{game_code}:players", 0, -1)
        players_data = [{
            "name": player,
            "score": int(r.get(f"game:{game_code}:score:{player}") or 0),
            "position": idx + 1,
            "is_admin": (r.get(f"game:{game_code}") == player)
        } for idx, player in enumerate(players)]
        return render_template("waiting.html",
                               game_code=game_code,
                               players=players,
                               players_data=players_data,
                               username=username,
                               admin=(r.get(f"game:{game_code}") == username),
                               nb_words=int(r.get(f"game:{game_code}:nb_words") or 1)
                               )

    flash(f"La partie '{game_code}' est inconnue.")
    return redirect(url_for("index"))


@app.route("/leavegame", methods=["POST"])
def leave_game():
    game_code = request.form.get("game_code") or request.cookies.get("game")
    username = request.cookies.get("username")

    if not username or not game_code:
        flash("Partie ou utilisateur introuvable.")
        return redirect(url_for("index"))

    if request.form.get("action") == "delete":
        if r.get(f"game:{game_code}") != username:
            flash("Vous n'êtes pas l'administrateur de cette salle.")
            return redirect(url_for("waiting"))
        delete_game_room(game_code)
        flash(f"La salle '{game_code}' a été supprimée.")
    else:
        players = r.lrange(f"game:{game_code}:players", 0, -1)
        if username in players:
            r.lrem(f"game:{game_code}:players", 0, username)
        r.srem(f"game:{game_code}:skip_votes", username)
        release_username(username)
        flash(f"Vous avez quitté la salle '{game_code}'.")

    resp = redirect(url_for("index"))
    resp.set_cookie("game", "", expires=0, secure=request.is_secure, httponly=True)
    return resp


@app.route("/game")
def game():
    game_code = request.cookies.get("game")
    username = request.cookies.get("username")

    if not game_code or not username:
        flash("Partie ou utilisateur introuvable.")
        return redirect(url_for("index"))

    if not r.exists(f"game:{game_code}:status") and not r.exists(f"game:{game_code}"):
        flash(f"La salle '{game_code}' a été supprimée.")
        resp = redirect(url_for("index"))
        resp.set_cookie("game", "", expires=0, secure=request.is_secure, httponly=True)
        return resp

    if r.get(f"game:{game_code}:status") == "waiting":
        return redirect(url_for("waiting"))

    if r.get(f"game:{game_code}:status") == "playing" and username in r.lrange(f"game:{game_code}:players", 0, -1):
        word = r.get(f"game:{game_code}:word")
        available_letters = r.lrange(f"game:{game_code}:{word}", 0, -1)

        display_word = "".join([
            char if char not in available_letters else (char if char not in all_letters else "X")
            for char in word
        ])

        playerplay = r.get(f"game:{game_code}:playerplay")
        listplayers = r.lrange(f"game:{game_code}:players", 0, -1)

        # Remet le tour à 0 au premier chargement ou si la liste des joueurs a rétréci depuis.
        if not playerplay or int(playerplay) >= len(listplayers):
            r.set(f"game:{game_code}:playerplay", 0, ex=3600)
            playerplay = 0

        ifplay = bool(listplayers) and listplayers[int(playerplay)] == username

        player_index = int(playerplay) if playerplay is not None else 0
        current_player_name = listplayers[player_index] if listplayers else username
        players_data = [{
            "name": player,
            "score": int(r.get(f"game:{game_code}:score:{player}") or 0),
            "position": idx + 1,
            "is_current": idx == player_index,
            "is_me": player == username
        } for idx, player in enumerate(listplayers)]

        last_event_raw = r.get(f"game:{game_code}:last_event")
        last_event = json.loads(last_event_raw) if last_event_raw else None

        skip_votes = set(r.smembers(f"game:{game_code}:skip_votes")) & set(listplayers)

        return render_template("game.html",
                              game_code=game_code,
                              username=username,
                              word=display_word,
                              voyelles=voyelles,
                              consonnes=consonnes,
                              available_letters=available_letters,
                              ifplay=ifplay if 'ifplay' in locals() else False,
                              playerplay=current_player_name,
                              listplayers=listplayers,
                              players_data=players_data,
                              admin=(r.get(f"game:{game_code}") == username),
                              money=int(r.get(f'game:{game_code}:money') or 0),
                              nb_words=int(r.get(f"game:{game_code}:nb_words") or 1),
                              last_event=last_event,
                              skip_votes_count=len(skip_votes),
                              has_voted_skip=username in skip_votes
                              )

    if r.get(f"game:{game_code}:status") == "finished":
        scoreboard = sorted(
            [{"name": player, "score": int(r.get(f"game:{game_code}:score:{player}") or 0)} for player in r.lrange(f"game:{game_code}:players", 0, -1)],
            key=lambda entry: entry["score"],
            reverse=True
        )
        for index, entry in enumerate(scoreboard):
            entry["is_winner"] = index == 0

        resp = make_response(render_template("finished.html", game_code=game_code, word=r.get(f"game:{game_code}:word"), listplayers=scoreboard, username=username, admin=(r.get(f"game:{game_code}") == username)))
        resp.set_cookie('game', '', expires=0)
        return resp

    flash(f"La partie '{game_code}' est inconnue.")
    return redirect(url_for("index"))


@app.route("/restartgame", methods=["POST"])
def restart_game():
    game_code = request.form.get("game_code")
    username = request.cookies.get("username")

    if not game_code or not username:
        flash("Partie ou utilisateur introuvable.")
        return redirect(url_for("index"))

    if r.get(f"game:{game_code}") != username:
        flash("Seul l'administrateur peut relancer la partie.")
        return redirect(url_for("index"))

    # On garde le salon et sa liste de joueurs, on efface uniquement l'état de la manche précédente.
    listplayers = r.lrange(f"game:{game_code}:players", 0, -1)
    old_word = r.get(f"game:{game_code}:word")

    for player in listplayers:
        r.delete(f"game:{game_code}:score:{player}")

    if old_word:
        r.delete(f"game:{game_code}:{old_word}")

    r.delete(f"game:{game_code}:word")
    r.delete(f"game:{game_code}:playerplay")
    r.delete(f"game:{game_code}:last_event")
    r.delete(f"game:{game_code}:skip_votes")
    r.set(f"game:{game_code}:status", "waiting", ex=3600)
    r.set(f"game:{game_code}:money", random.randrange(50, 1000, 50), ex=3600)
    r.expire(f"game:{game_code}", 3600)
    r.expire(f"game:{game_code}:players", 3600)

    resp = redirect(url_for("waiting"))
    resp.set_cookie("game", game_code, max_age=3600, secure=request.is_secure, httponly=True)
    return resp




@app.route("/guess", methods=["POST"])
def guess():
    game_code = request.form.get("game_code")
    username = request.cookies.get("username")
    if not game_code or not username:
        flash("Partie ou utilisateur introuvable")
        return redirect(url_for("game"))

    listplayers = r.lrange(f"game:{game_code}:players", 0, -1)
    playerplay = r.get(f"game:{game_code}:playerplay")
    if not playerplay or not listplayers:
        flash("La partie est inconnue ou inactive.")
        return redirect(url_for("game"))

    if request.form.get("action") == "skip":
        if username not in listplayers:
            flash("Vous ne faites pas partie de cette partie.")
            return redirect(url_for("game"))

        # Vote de passage ouvert à tout moment, indépendamment du tour de jeu.
        r.sadd(f"game:{game_code}:skip_votes", username)
        active_votes = set(r.smembers(f"game:{game_code}:skip_votes")) & set(listplayers)

        if len(active_votes) >= len(listplayers):
            r.delete(f"game:{game_code}:skip_votes")
            current_word = r.get(f"game:{game_code}:word")
            load_new_round_word(game_code, current_word)
            # Le vote de passage change juste le mot, le joueur en cours garde la main.
            flash("Tous les joueurs ont choisi de passer. Aucun point n'a été attribué et un nouveau mot a été choisi.")
        else:
            flash(f"{username} a voté pour passer ce mot ({len(active_votes)}/{len(listplayers)} votes).")

        return redirect(url_for("game"))

    if listplayers[int(playerplay)] != username:
        flash(f"Ce n'est pas votre tour de jouer, c'est le tour de {listplayers[int(playerplay)]}.")
        return redirect(url_for("game"))

    if request.form.get("letter"):
        letter = request.form.get("letter")

        if not letter:
            flash("Lettre introuvable")
            return redirect(url_for("game"))

        word = r.get(f"game:{game_code}:word")
        available_letters = r.lrange(f"game:{game_code}:{word}", 0, -1)

        if letter in available_letters:
            
            if letter in voyelles:
                # Les voyelles se "payent" 2500 points, contrairement aux consonnes qui sont gratuites.
                if int(r.get(f"game:{game_code}:score:{username}") or 0) < 2500:
                    flash(f"Vous n'avez pas assez d'argent pour prendre une voyelle. Il vous faut 2500, vous avez {int(r.get(f'game:{game_code}:score:{username}') or 0)}.")
                    return redirect(url_for("game"))
                else:
                    score = word.count(letter)  # Récupère le nombre de lettres du mot pour le score
                    r.lrem(f"game:{game_code}:{word}", 0, letter)  # Supprime la lettre de la liste des lettres disponibles
                    r.set(f"game:{game_code}:score:{username}", int(r.get(f"game:{game_code}:score:{username}") or 0) - 2500, ex=3600)
                    if score == 0:
                        flash(f"La lettre '{letter}' n'est pas dans le mot. Vous avez perdu 2500.")
                        r.set(f"game:{game_code}:playerplay", (int(playerplay) + 1) % len(listplayers), ex=3600)  # Passe au joueur suivant

            else:
                score = word.count(letter)  # Récupère le nombre de lettres du mot pour le score
                r.lrem(f"game:{game_code}:{word}", 0, letter)  # Supprime la lettre de la liste des lettres disponibles
                r.set(f"game:{game_code}:score:{username}", int(r.get(f"game:{game_code}:score:{username}") or 0) + score * int(r.get(f"game:{game_code}:money") or 100), ex=3600)
                r.set(f'game:{game_code}:money', random.randrange(50, 1000, 50), ex=3600)  # Donne de l'argent aléatoire au joueur suivant
                if score == 0:
                    flash(f"La lettre '{letter}' n'est pas dans le mot. Vous n'avez rien gagné.")
                    r.set(f"game:{game_code}:playerplay", (int(playerplay) + 1) % len(listplayers), ex=3600)  # Passe au joueur suivant
            return redirect(url_for("game"))
        else:
            flash(f"La lettre '{letter}' n'est pas disponible pour cette partie.")
            return redirect(url_for("game"))

    elif request.form.get("text"):
        text = request.form.get("text")
        if not text:
            flash("Texte introuvable")
            return redirect(url_for("game"))

        word = r.get(f"game:{game_code}:word")
        if not word:
            flash("Mot de la partie introuvable (partie corrompue ou expirée).")
            return redirect(url_for("game"))
        nb_words = r.get(f"game:{game_code}:nb_words") or 1

        if text.lower() == word.lower():
            r.delete(f"game:{game_code}:skip_votes")
            final_message = f"Félicitations {username}, vous avez deviné le mot '{word}'."
            r.set(f"game:{game_code}:last_event", json.dumps({
                "player": username,
                "word": word,
                "message": final_message,
                "time": int(time.time())
            }), ex=6)
            r.set(f"game:{game_code}:nb_words", int(nb_words) - 1, ex=3600)
            if int(nb_words) - 1 <= 0:
                r.set(f"game:{game_code}:status", "finished", ex=3600)
                flash(f"{final_message} La partie est terminée.")
                return redirect(url_for("game"))
            else:
                score = len(word)
                flash(f"{final_message} Il reste {int(nb_words) - 1} mots à deviner.")
                r.set(f"game:{game_code}:score:{username}", int(r.get(f"game:{game_code}:score:{username}") or 0) + score * int(r.get(f"game:{game_code}:money") or 100), ex=3600)
                load_new_round_word(game_code, word)
                # Le gagnant du mot rejoue en premier sur le mot suivant.
                r.set(f"game:{game_code}:playerplay", listplayers.index(username), ex=3600)
                return redirect(url_for("game"))
        else:
            flash(f"Désolé {username}, ce n'est pas le bon mot.")
            r.set(f"game:{game_code}:playerplay", (int(playerplay) + 1) % len(listplayers), ex=3600)
            return redirect(url_for("game"))

    flash("Aucune action valide n'a été fournie.")
    return redirect(url_for("game"))

## debug route to get all redis keys and values
@app.route('/getredis')
def get_redis():
    keys = r.keys()
    values = {key: r.get(key) for key in keys}
    return values


## Curseurs des joueurs, façon Figma/Canva : chacun envoie sa position, les autres la récupèrent en direct.
@app.route('/api/cursor', methods=['POST'])
def update_cursor():
    game_code = request.form.get("game_code")
    username = request.cookies.get("username")
    x = request.form.get("x")
    y = request.form.get("y")

    if not game_code or not username or x is None or y is None:
        return {"error": "game_code, x et y sont requis"}, 400

    try:
        x = max(0.0, min(100.0, float(x)))
        y = max(0.0, min(100.0, float(y)))
    except ValueError:
        return {"error": "x et y doivent être numériques"}, 400

    # Expiration courte : le curseur disparaît vite si l'onglet est fermé ou inactif.
    r.set(f"game:{game_code}:cursor:{username}", json.dumps({"x": x, "y": y}), ex=5)
    return {"ok": True}, 200


@app.route('/api/cursors', methods=['POST'])
def list_cursors():
    game_code = request.form.get("game_code")

    if not game_code:
        return {"error": "game_code is required"}, 400

    admin_username = r.get(f"game:{game_code}")
    listplayers = r.lrange(f"game:{game_code}:players", 0, -1)
    playerplay = r.get(f"game:{game_code}:playerplay")
    current_player_name = None
    if listplayers and playerplay is not None:
        index = int(playerplay) if int(playerplay) < len(listplayers) else 0
        current_player_name = listplayers[index]

    # Une seule salle a un nombre de curseurs limité au nombre de joueurs, KEYS reste donc bon marché ici.
    prefix = f"game:{game_code}:cursor:"
    cursors = []
    for key in r.keys(f"{prefix}*"):
        username = key[len(prefix):]
        raw = r.get(key)
        if not raw:
            continue
        try:
            position = json.loads(raw)
        except (TypeError, ValueError):
            continue
        cursors.append({
            "username": username,
            "x": position.get("x", 0),
            "y": position.get("y", 0),
            "is_admin": username == admin_username,
            "is_current": username == current_player_name
        })

    return {"cursors": cursors}, 200

## API route to update game data, can be used for a signal page or other purposes
## if update sinal page api 
@app.route('/api/update/<type>', methods=['POST'])
def api_update(type):
    game_code = request.form.get("game_code")

    if not game_code:
        return {"error": "game_code is required"}, 400

    game_data = {
        "status": r.get(f"game:{game_code}:status"),
        "word": r.get(f"game:{game_code}:word"),
        "playerplay": r.get(f"game:{game_code}:playerplay"),
        "players": r.lrange(f"game:{game_code}:players", 0, -1),
        "money": r.get(f"game:{game_code}:money"),
        "nb_words": r.get(f"game:{game_code}:nb_words")
    }

    # Générer un hash qui correspond à l'état de la partie
    if type == "hash":
        game_hash = generate_game_hash(game_code)
        return {"hash": game_hash}, 200
    else:
        return game_data, 200






if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", debug=debug_mode, threaded=True, port=port)
