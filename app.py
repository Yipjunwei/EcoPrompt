from flask import Flask, render_template, request, jsonify
import google.generativeai as genai
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-2.0-flash")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/clean", methods=["POST"])
def clean():
    data = request.get_json(silent=True) or {}
    query = data.get("query", "")

    if not query:
        return jsonify({"error": "No query provided"}), 400

    try:
        response = model.generate_content(query)
        output = response.text
        tokens_saved = max(0, len(query.split()) // 3)

        return jsonify({
            "output": output,
            "query": query,
            "tokens_saved": tokens_saved
        })

    except Exception as e:
        print(f"ERROR: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)