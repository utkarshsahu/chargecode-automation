from flask import Flask, request, jsonify, send_from_directory
import os
from workflow import run_workflow  # <-- your workflow logic lives here

port = int(os.environ.get('PORT', 4000))
app = Flask(__name__, static_folder="static", template_folder="templates")

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# -------------------------
# GET: Render index.html
# -------------------------
@app.route("/", methods=["GET"])
def index():
    return send_from_directory("templates", "index.html")


# -------------------------
# POST: Upload voice & run workflow
# -------------------------
@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    filepath = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(filepath)

    try:
        result = run_workflow(filepath)  # call your workflow
        return jsonify(result)  # return JSON directly
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=port)

