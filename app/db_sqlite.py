import sqlite3

# Tên file database sẽ được tạo ngay trong thư mục code
DB_FILE = "english_app.db"

def get_connection():
    """Hàm tạo và mở kết nối tới database"""
    conn = sqlite3.connect(DB_FILE)
    # Cấu hình trả về kết quả dạng dictionary để dễ gọi tên cột (thay vì tuple)
    conn.row_factory = sqlite3.Row
    return conn

def init_tables():
    """Khởi tạo bảng dữ liệu nếu chưa tồn tại"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vocabularies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word TEXT NOT NULL,
            meaning TEXT NOT NULL,
            part_of_speech TEXT,
            example TEXT
        )
    """)
    conn.commit()
    conn.close()

# Thêm dữ liệu mẫu
def insert_word(word, meaning, part_of_speech="", example=""):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO vocabularies (word, meaning, part_of_speech, example) VALUES (?, ?, ?, ?)",
        (word, meaning, part_of_speech, example)
    )
    conn.commit()
    conn.close()

# Lấy toàn bộ dữ liệu
def fetch_all_words():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM vocabularies")
    rows = cursor.fetchall()
    conn.close()
    return rows

if __name__ == "__main__":
    init_tables()
    insert_word("Tenacious", "Kiên trì", "adj", "Never give up.")
    for row in fetch_all_words():
        print(dict(row))