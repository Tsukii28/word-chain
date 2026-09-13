import sqlite3
import random
from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)
DB_NAME = "database.db"

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vocabularies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word TEXT NOT NULL,
                part_of_speech TEXT,
                meaning TEXT NOT NULL,
                example TEXT
            )
        """)

@app.route("/")
def index():
    conn = get_db()
    words = conn.execute("SELECT * FROM vocabularies ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("index.html", words=words)

@app.route("/add", methods=["POST"])
def add_word():
    word = request.form.get("word")
    part_of_speech = request.form.get("part_of_speech")
    meaning = request.form.get("meaning")
    example = request.form.get("example")

    if word and meaning:
        conn = get_db()
        conn.execute(
            "INSERT INTO vocabularies (word, part_of_speech, meaning, example) VALUES (?, ?, ?, ?)",
            (word.strip(), part_of_speech.strip(), meaning.strip(), example.strip())
        )
        conn.commit()
        conn.close()
    return redirect(url_for("index"))

@app.route("/delete/<int:item_id>")
def delete_word(item_id):
    conn = get_db()
    conn.execute("DELETE FROM vocabularies WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))

@app.route("/quiz")
def quiz():
    conn = get_db()
    all_words = conn.execute("SELECT * FROM vocabularies").fetchall()
    conn.close()

    if len(all_words) < 4:
        return "Cần tối thiểu 4 từ vựng trong cơ sở dữ liệu để tạo bài trắc nghiệm!"

    # Chọn ngẫu nhiên 1 từ làm câu hỏi và 3 từ làm phương án gây nhiễu
    target = random.choice(all_words)
    wrong_options = [w for w in all_words if w["id"] != target["id"]]
    distractors = random.sample(wrong_options, 3)

    options = distractors + [target]
    random.shuffle(options)

    return render_template("quiz.html", question=target, options=options)

if __name__ == "__main__":
    init_db()
    app.run(debug=True)