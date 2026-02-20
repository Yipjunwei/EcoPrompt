from flask import Flask, render_template, request, jsonify

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/clean", methods=["POST"])
def clean():
    data = request.get_json(silent=True) or {}
    query = data.get("query", "")
    # TODO: wire up to your LLM / ChatGPT wrapper here
    return jsonify({"output": "TODO", "query": query})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
