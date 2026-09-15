from flask import Flask, Response, render_template_string, make_response, request
import random
import redis
import os

# Connexion à Redis
r = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    decode_responses=True
)

# Test
r.set("test", "OK")
print(r.get("test"))  # Doit afficher "OK"

app = Flask(__name__)

@app.route("/")
def index():
    return render_template_string("test")

@app.route("/newgame")
def newgame():
    code = random.randrange(1111,9999,1)
    resp = make_response("Cookie has been set!")
    resp.set_cookie('game', str(code), max_age=3600, secure=True, httponly=True)
    return resp

@app.route('/getgame')
def get_cookie():
    username = request.cookies.get('game')
    return f"Hello {username}" if username else "No cookie found"


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, threaded=True, port=8000)