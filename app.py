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




from flask import Flask, request, jsonify
import requests


GOOGLE_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbzUdZmvsDQBSpHAJMKnaUPhvTpGhkJ-vGCDPg4Yc4ruq2wA2zEw-A4kMRRkCVOTitJ9/exec"


@app.route('/send-message', methods=['POST'])
def send_message():

    data = {
        "your-name": request.form.get("your-name"),
        "your-number": request.form.get("your-number"),
        "your-email": request.form.get("your-email"),
        "message": request.form.get("message")
    }

    requests.post(GOOGLE_SCRIPT_URL, data=data)

    return "Message sent successfully"



if __name__ == "__main__":
    app.run(debug=True)