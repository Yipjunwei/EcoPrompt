from flask import Flask, render_template, request, jsonify, session
from groq import Groq
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key")
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.1-8b-instant"


@app.before_request
def log_request():
    print("REQ", request.method, request.path)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/clean", methods=["POST"])
def clean():
    data = request.get_json(silent=True) or {}
    query = data.get("query", "")

    if not query:
        return jsonify({"error": "No query provided"}), 400

    # Initialize history in session if not present
    if "history" not in session:
        session["history"] = []

    # Append the new user message
    session["history"].append({"role": "user", "content": query})
    session.modified = True

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=session["history"]
        )
        output = response.choices[0].message.content
        tokens_saved = max(0, len(query.split()) // 3)

        # Append assistant reply to history
        session["history"].append({"role": "assistant", "content": output})
        session.modified = True

        return jsonify({
            "output": output,
            "query": query,
            "tokens_saved": tokens_saved
        })

    except Exception as e:
        print(f"ERROR: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/new", methods=["POST"])
def new_conversation():
    session.pop("history", None)
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, port=5000)