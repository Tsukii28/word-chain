import os
import asyncio
from typing import Dict, List, Optional
import urllib.request

ENGLISH_WORDS: set[str] = set()

def load_english_words():
    global ENGLISH_WORDS
    url = "https://raw.githubusercontent.com/dwyl/english-words/master/words_alpha.txt"
    try:
        print(">>> Đang nạp từ điển tiếng Anh...")
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read().decode('utf-8')
            ENGLISH_WORDS = {line.strip().lower() for line in content.splitlines() if len(line.strip()) >= 2}
        print(f">>> [TỪ ĐIỂN] Nạp thành công {len(ENGLISH_WORDS)} từ!")
    except Exception as e:
        print(f">>> [LỖI NẠP TỪ]: {e}, dùng tạm danh sách mở rộng.")
        ENGLISH_WORDS = {
            "apple", "elephant", "tiger", "rabbit", "table", "egg", "game", 
            "orange", "energy", "yellow", "water", "river", "road", "dog", "cat",
            "time", "year", "people", "way", "day", "man", "thing", "woman", "life", "child"
        }

# Gọi nạp từ ngay khi import module
load_english_words()
class Player:
    def __init__(self, sid: str, username: str):
        self.sid = sid
        self.username = username
        self.is_ready = False
        self.score = 0
        self.lives = 3

    def to_dict(self):
        return {
            "sid": self.sid,
            "username": self.username,
            "is_ready": self.is_ready,
            "score": self.score,
            "lives": self.lives
        }

from typing import List, Optional
import asyncio

class Room:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.players: List[Player] = []
        self.status = "WAITING"  # "WAITING" | "PLAYING" | "ENDED"
        self.current_turn_index = 0
        self.last_word = ""
        self.last_ipa = ""        # Đặt đúng vị trí trong __init__
        self.last_meaning = ""    # Đặt đúng vị trí trong __init__
        self.used_words = set()
        self.timer_task: Optional[asyncio.Task] = None

    def add_player(self, player: Player):
        self.players.append(player)

    def remove_player(self, sid: str):
        self.players = [p for p in self.players if p.sid != sid]

    def get_player(self, sid: str) -> Optional[Player]:
        for p in self.players:
            if p.sid == sid:
                return p
        return None

    def current_player(self) -> Optional[Player]:
        if not self.players or self.current_turn_index >= len(self.players):
            return None
        return self.players[self.current_turn_index]

    def validate_and_apply_word(self, sid: str, word: str) -> tuple[bool, str]:
        curr = self.current_player()
        if not curr or curr.sid != sid:
            return False, "Chưa đến lượt của bạn!"

        # KHAI BÁO BIẾN TRƯỚC TIÊN TẠI ĐÂY:
        clean_word = word.strip().lower()

        # 1. Kiểm tra từ rỗng
        if not clean_word:
            return False, "Vui lòng nhập một từ!"

        # 2. Kiểm tra từ trong từ điển
        if clean_word not in ENGLISH_WORDS:
            return False, "Từ không có trong từ điển tiếng Anh!"

        # 3. Kiểm tra ký tự nối tiếp
        if self.last_word:
            required_char = self.last_word[-1].lower()
            if not clean_word.startswith(required_char):
                return False, f"Từ phải bắt đầu bằng chữ '{required_char}'!"

        # 4. Kiểm tra trùng lặp với từ đã dùng
        if clean_word in self.used_words:
            return False, "Từ này đã được sử dụng trong phòng!"

        # 5. Ghi nhận từ hợp lệ và cộng điểm
        self.used_words.add(clean_word)
        self.last_word = clean_word
        curr.score += 10
        self.current_turn_index = (self.current_turn_index + 1) % len(self.players)
        return True, "Thành công"

    def to_dict(self):
        """Chuyển thông tin phòng thành dict gửi qua Socket.IO."""
        return {
            "room_id": self.room_id,
            "status": self.status,
            "last_word": self.last_word,
            "last_ipa": self.last_ipa,          # Trả về IPA cho client
            "last_meaning": self.last_meaning,  # Trả về Nghĩa cho client
            "current_player_sid": self.current_player().sid if self.current_player() else None,
            "players": [p.to_dict() for p in self.players]
        }