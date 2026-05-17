from flask import Flask, render_template
from route1 import bp1  # ou le nom de votre fichier blueprint
from route2 import bp2  # ou le nom de votre fichier blueprint

app = Flask(__name__)

#app.register_blueprint(bp)

app.register_blueprint(bp1)
app.register_blueprint(bp2)


@app.route("/")
def index():
    return render_template("index.html")

#if __name__ == "__main__":
#    app.run(debug=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
    