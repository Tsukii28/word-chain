import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

import httpx
import socketio
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from auth import (
    create_access_token,
    decode_token,
    get_current_user,
    hash_password,
    verify_password,
)
from database import SessionLocal, UserModel, get_db, init_db
from game_logic import Player, Room
from models import (
    CreateRoomRequest,
    RoomResponse,
    TokenResponse,
    UserLoginRequest,
    UserProfileResponse,
    UserRegisterRequest,
)

# Khởi tạo DB table
init_db()

# HTTP Client dùng chung (tái sử dụng connection pool)
http_client: Optional[httpx.AsyncClient] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        },
        timeout=4.0,
        follow_redirects=True,
    )
    yield
    await http_client.aclose()
    # Dọn dẹp tất cả timer đang chạy nếu server tắt
    for task in room_timers.values():
        if not task.done():
            task.cancel()

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
app = FastAPI(title="English Word Chain Game", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

rooms_db: dict[str, Room] = {}
token_stats = {"total_tokens_issued": 0, "active_users": set()}

TURN_DURATION = 20
room_timers: dict[str, asyncio.Task] = {}

# ================= TỪ ĐIỂN & TRA CỨU IPA / TIẾNG VIỆT =================

WORD_CACHE: dict[str, dict] = {
    "apple": {"ipa": "/ˈæp.əl/", "meaning": "quả táo"}
}

async def get_clean_ipa(word_clean: str, client: httpx.AsyncClient) -> str:
    try:
        url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word_clean}"
        res = await client.get(url)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and len(data) > 0:
                entry = data[0]
                if entry.get("phonetic"):
                    return entry["phonetic"]
                for item in entry.get("phonetics", []):
                    ipa_text = item.get("text", "").strip()
                    if ipa_text:
                        return ipa_text
    except Exception:
        pass
    return f"/{word_clean}/"

async def fetch_word_details_fast(word: str) -> dict:
    word_clean = word.strip().lower()

    if word_clean in WORD_CACHE:
        return WORD_CACHE[word_clean]

    client = http_client or httpx.AsyncClient(timeout=4.0)

    task_ipa = get_clean_ipa(word_clean, client)

    async def get_meaning():
        try:
            params = {"client": "gtx", "sl": "en", "tl": "vi", "dt": "t", "q": word_clean}
            r = await client.get(
                "https://translate.googleapis.com/translate_a/single", params=params
            )
            if r.status_code == 200:
                d = r.json()
                if d and d[0] and d[0][0]:
                    return d[0][0][0]
        except Exception as e:
            print(f">>> [LỖI DỊCH NGHĨA]: {e}")
        return "Từ vựng tiếng Anh"

    results = await asyncio.gather(task_ipa, get_meaning(), return_exceptions=True)

    ipa_res = results[0] if isinstance(results[0], str) else f"/{word_clean}/"
    meaning_res = results[1] if isinstance(results[1], str) else "Từ vựng tiếng Anh"

    res_data = {"ipa": ipa_res, "meaning": meaning_res}
    WORD_CACHE[word_clean] = res_data
    return res_data

fetch_word_details = fetch_word_details_fast

# ================= REST APIS =================

@app.post("/api/auth/register", status_code=201, tags=["Auth"])
def register(req: UserRegisterRequest, db: Session = Depends(get_db)):
    existing_user = db.query(UserModel).filter(UserModel.username == req.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Tên đăng nhập đã tồn tại")

    new_user = UserModel(
        username=req.username,
        password_hash=hash_password(req.password),
        score=0,
        games_played=0,
        games_won=0,
    )
    db.add(new_user)
    db.commit()
    return {"message": "Đăng ký thành công"}

@app.post("/api/auth/login", response_model=TokenResponse, tags=["Auth"])
def login(req: UserLoginRequest, db: Session = Depends(get_db)):
    user = db.query(UserModel).filter(UserModel.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Sai tài khoản hoặc mật khẩu")

    token = create_access_token(data={"sub": user.username})
    token_stats["total_tokens_issued"] += 1
    token_stats["active_users"].add(user.username)
    return TokenResponse(access_token=token, username=user.username)

@app.get("/api/rooms", tags=["Rooms"])
def get_rooms():
    return [
        {
            "room_id": r.room_id,
            "status": r.status,
            "player_count": len(r.players),
            "last_word": r.last_word,
        }
        for r in rooms_db.values()
    ]

@app.get("/api/user/me", tags=["User"])
def get_me(username: str = Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.query(UserModel).filter(UserModel.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
    return {
        "username": user.username,
        "score": user.score,
        "games_played": user.games_played,
        "games_won": user.games_won,
    }

@app.get("/api/leaderboard", tags=["Leaderboard"])
def get_leaderboard(db: Session = Depends(get_db)):
    top_users = db.query(UserModel).order_by(UserModel.score.desc()).limit(10).all()
    return [
        {
            "username": u.username,
            "score": u.score,
            "games_won": u.games_won,
        }
        for u in top_users
    ]

# ================= TIMER & LOGIC ĐỔI LƯỢT =================

def stop_room_timer(room_id: str):
    """Hủy timer hiện tại của phòng."""
    if room_id in room_timers and not room_timers[room_id].done():
        room_timers[room_id].cancel()
    room_timers.pop(room_id, None)

def switch_turn(room: Room):
    """Chuyển lượt sang người còn sống tiếp theo."""
    if not room or not room.players:
        return None, None

    old_player = room.current_player()
    
    # Tìm người tiếp theo còn mạng sống (lives > 0)
    for _ in range(len(room.players)):
        room.current_turn_index = (room.current_turn_index + 1) % len(room.players)
        cand = room.current_player()
        if cand and cand.lives > 0:
            return old_player, cand

    return old_player, None

async def start_turn_timer(room_id: str):
    room_id = str(room_id).strip()
    stop_room_timer(room_id)

    async def _timer_worker():
        try:
            for remaining in range(TURN_DURATION, -1, -1):
                await sio.emit("timer_tick", {"seconds": remaining}, room=room_id)
                if remaining == 0:
                    break
                await asyncio.sleep(1)

            room = rooms_db.get(room_id)
            if not room or room.status != "PLAYING":
                return

            timed_out_player = room.current_player()
            if timed_out_player:
                timed_out_player.lives = max(0, timed_out_player.lives - 1)

            # Kiểm tra số người còn sống (lives > 0)
            alive_players = [p for p in room.players if p.lives > 0]
            if len(alive_players) <= 1:
                room.status = "ENDED"
                winner = alive_players[0] if alive_players else None
                await sio.emit("game_over", {
                    "winner": winner.username if winner else "Hòa",
                    "reason": f"{timed_out_player.username} hết thời gian và hết mạng!"
                }, room=room_id)
                await sio.emit("room_updated", room.to_dict(), room=room_id)
                return

            old_p, new_p = switch_turn(room)
            if not new_p:
                return

            await sio.emit("turn_timeout", {
                "timed_out_sid": old_p.sid if old_p else "",
                "timed_out_user": old_p.username if old_p else "",
                "lives_left": old_p.lives if old_p else 0,
                "next_user": new_p.username,
            }, room=room_id)

            await sio.emit("room_updated", room.to_dict(), room=room_id)
            await start_turn_timer(room_id)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f">>> [LỖI TIMER {room_id}]: {e}")

    room_timers[room_id] = asyncio.create_task(_timer_worker())

# ================= SOCKET.IO EVENTS =================

@sio.event
async def connect(sid, environ, auth):
    token = auth.get("token") if auth else None
    username = decode_token(token) if token else None

    if not username:
        return False

    await sio.save_session(sid, {"username": username})

@sio.event
async def join_room(sid, data):
    session = await sio.get_session(sid)
    username = session.get("username", f"User_{sid[:4]}")
    room_id = str(data.get("room_id", "room-1")).strip()

    if room_id not in rooms_db:
        rooms_db[room_id] = Room(room_id)

    room = rooms_db[room_id]
    await sio.enter_room(sid, room_id)

    existing_player = room.get_player(sid)
    if not existing_player:
        room.add_player(Player(sid, username))

    await sio.emit("room_updated", room.to_dict(), room=room_id)

@sio.event
async def send_chat(sid, data):
    room_id = str(data.get("room_id", "")).strip()
    msg = str(data.get("message", "")).strip()
    if not room_id or not msg:
        return

    session = await sio.get_session(sid)
    username = session.get("username", "Khách")

    await sio.emit(
        "new_chat_message",
        {"sender": username, "message": msg, "is_me_sid": sid},
        room=room_id,
    )

@sio.event
async def toggle_ready(sid, data):
    room_id = data.get("room_id", "room-1")
    room = rooms_db.get(room_id)
    if not room:
        return

    player = room.get_player(sid)
    if player:
        player.is_ready = not player.is_ready

    ready_count = sum(1 for p in room.players if p.is_ready)
    if len(room.players) >= 2 and ready_count == len(room.players) and room.status == "WAITING":
        room.status = "PLAYING"
        # Reset mạng và điểm trận mới cho người chơi
        for p in room.players:
            p.lives = 3

        if not room.last_word:
            room.last_word = "apple"
            info = await fetch_word_details_fast("apple")
            room.last_ipa = info["ipa"]
            room.last_meaning = info["meaning"]

        await sio.emit(
            "game_started",
            {"message": f"Trận đấu bắt đầu! Từ khởi đầu: '{room.last_word}'"},
            room=room_id,
        )
        await sio.emit("room_updated", room.to_dict(), room=room_id)
        await start_turn_timer(room_id)
    else:
        await sio.emit("room_updated", room.to_dict(), room=room_id)

@sio.event
async def submit_word(sid, data):
    room_id = str(data.get("room_id", "")).strip()
    word = str(data.get("word", "")).strip().lower()

    room = rooms_db.get(room_id)
    if not room or room.status != "PLAYING":
        return

    session = await sio.get_session(sid)
    username = session.get("username", "Người chơi")

    # 1. KIỂM TRA HỢP LỆ VÀ ĐỔI LƯỢT NGAY LẬP TỨC (Không chờ API)
    # Tạm thời gán placeholder để hiển thị tức thì
    temp_ipa = f"/{word}/"
    temp_meaning = "Đang tra nghĩa..."

    valid, msg = room.validate_and_apply_word(
        sid, word, ipa=temp_ipa, meaning=temp_meaning
    )
    if not valid:
        await sio.emit("error_message", {"message": msg}, to=sid)
        return

    # 2. PHÁT NGAY LẬP TỨC CHO CẢ PHÒNG & KÍCH HOẠT LƯỢT TIẾP THEO (Tíc tắc ~5-10ms)
    await sio.emit("word_accepted", {
        "username": username,
        "word": word,
        "last_word": room.last_word,
        "ipa": temp_ipa,
        "meaning": temp_meaning
    }, room=room_id)

    await sio.emit("room_updated", room.to_dict(), room=room_id)
    await start_turn_timer(room_id)

    # 3. CHẠY NGẦM (BACKGROUND): Tra cứu IPA/Nghĩa thật & Lưu DB
    async def _fetch_and_broadcast_details(target_word: str):
        info = await fetch_word_details_fast(target_word)
        # Nếu từ này vẫn là từ gần nhất của phòng thì cập nhật
        if room.last_word == target_word:
            room.last_ipa = info["ipa"]
            room.last_meaning = info["meaning"]
            await sio.emit("word_details_updated", {
                "word": target_word,
                "ipa": info["ipa"],
                "meaning": info["meaning"]
            }, room=room_id)

    asyncio.create_task(_fetch_and_broadcast_details(word))

    # Lưu điểm SQLite
    try:
        with SessionLocal() as db:
            db_user = db.query(UserModel).filter(UserModel.username == username).first()
            if db_user:
                db_user.score += 10
                db.commit()
    except Exception as e:
        print(f">>> [LỖI LƯU ĐIỂM]: {e}")

@sio.event
async def disconnect(sid):
    for r_id, room in list(rooms_db.items()):
        leaving_player = room.get_player(sid)
        if leaving_player:
            current_before_remove = room.current_player()
            was_their_turn = (current_before_remove and current_before_remove.sid == sid)

            room.remove_player(sid)
            await sio.leave_room(sid, r_id)

            # Nếu phòng trống -> dọn dẹp
            if not room.players:
                stop_room_timer(r_id)
                del rooms_db[r_id]
                break

            # Nếu chỉ còn 1 người khi đang chơi -> kết thúc trận
            if room.status == "PLAYING" and len(room.players) == 1:
                stop_room_timer(r_id)
                room.status = "ENDED"
                winner = room.players[0]
                await sio.emit("game_over", {
                    "winner": winner.username,
                    "reason": f"{leaving_player.username} đã rời phòng!"
                }, room=r_id)
            elif room.status == "PLAYING" and was_their_turn:
                # Nếu người thoát đang giữ lượt, chuyển ngay cho người kế
                await start_turn_timer(r_id)

            await sio.emit("room_updated", room.to_dict(), room=r_id)
            break

# ================= GIAO DIỆN CHƠI GAME =================

@app.get("/", response_class=HTMLResponse)
def index_page():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    html_file = os.path.join(current_dir, "index.html")

    if not os.path.exists(html_file):
        return HTMLResponse(f"<h3>Chưa tìm thấy file index.html tại: {html_file}</h3>")

    with open(html_file, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

combined_app = socketio.ASGIApp(sio, app)

if __name__ == "__main__":
    uvicorn.run("main:combined_app", host="0.0.0.0", port=8000, reload=True)