import os
import uuid
import asyncio
import httpx
import uvicorn
import socketio
from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from models import (
    UserRegisterRequest, UserLoginRequest, TokenResponse,
    UserProfileResponse, CreateRoomRequest, RoomResponse
)
from auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, decode_token
)
from game_logic import Room, Player
from database import init_db, get_db, UserModel, SessionLocal

# Khởi tạo bảng dữ liệu
init_db()

# Khởi tạo Socket.IO Async Server & FastAPI
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
app = FastAPI(title="English Word Chain Game")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

fake_users_db = {}
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

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }

    # Nới timeout lên 3.5s để server Render không bị đứt kết nối sớm
    async with httpx.AsyncClient(headers=headers, timeout=3.5, follow_redirects=True) as client:
        task_ipa = get_clean_ipa(word_clean, client)

        async def get_meaning():
            try:
                params = {"client": "gtx", "sl": "en", "tl": "vi", "dt": "t", "q": word_clean}
                r = await client.get("https://translate.googleapis.com/translate_a/single", params=params)
                if r.status_code == 200:
                    d = r.json()
                    if d and d[0] and d[0][0]:
                        return d[0][0][0]
            except Exception as e:
                print(f">>> [LỖI DỊCH NGHĨA]: {e}")
            return "Từ vựng tiếng Anh"

        results = await asyncio.gather(task_ipa, get_meaning(), return_exceptions=True)
        
        # Kiểm tra kết quả bọc an toàn chống exception
        ipa_res = results[0] if isinstance(results[0], str) else f"/{word_clean}/"
        meaning_res = results[1] if isinstance(results[1], str) else "Từ vựng tiếng Anh"

    res_data = {"ipa": ipa_res, "meaning": meaning_res}
    WORD_CACHE[word_clean] = res_data
    print(f">>> [TRA TỪ XONG] {word_clean} -> IPA: {ipa_res}, Nghĩa: {meaning_res}")
    return res_data

# Alias đảm bảo tương thích mọi hàm gọi
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
        games_won=0
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
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
            "last_word": r.last_word
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
        "games_won": user.games_won
    }

@app.get("/api/leaderboard", tags=["Leaderboard"])
def get_leaderboard(db: Session = Depends(get_db)):
    top_users = db.query(UserModel).order_by(UserModel.score.desc()).limit(10).all()
    return [
        {
            "username": u.username,
            "score": u.score,
            "games_won": u.games_won
        }
        for u in top_users
    ]

# ================= TIMER & LOGIC ĐỔI LƯỢT =================

def switch_turn(room):
    """Đổi lượt theo current_turn_index của Room."""
    if not room or not room.players:
        return None, None

    old_player = room.current_player()
    room.current_turn_index = (room.current_turn_index + 1) % len(room.players)
    new_player = room.current_player()
    return old_player, new_player

async def start_turn_timer(room_id: str):
    room_id = str(room_id).strip()

    if room_id in room_timers and not room_timers[room_id].done():
        room_timers[room_id].cancel()

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

            old_p, new_p = switch_turn(room)
            if not old_p or not new_p:
                return

            print(f">>> [TIMER] Hết giờ! Chuyển từ {old_p.username} sang {new_p.username}")

            # 1. Phát thông báo mất lượt cho cả phòng
            await sio.emit("turn_timeout", {
                "timed_out_sid": old_p.sid,
                "timed_out_user": old_p.username,
                "next_user": new_p.username
            }, room=room_id)

            # 2. Đồng bộ room mới nhất
            await sio.emit("room_updated", room.to_dict(), room=room_id)

            # 3. Kích hoạt đếm ngược 20s cho người vừa nhận lượt
            await start_turn_timer(room_id)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f">>> [LỖI TIMER]: {e}")

    room_timers[room_id] = asyncio.create_task(_timer_worker())

# ================= SOCKET.IO EVENTS =================

@sio.event
async def connect(sid, environ, auth):
    token = auth.get("token") if auth else None
    username = decode_token(token) if token else None
    
    if not username:
        return False
        
    await sio.save_session(sid, {"username": username})
    print(f"User {username} kết nối với Socket ID: {sid}")

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

    await sio.emit("new_chat_message", {
        "sender": username,
        "message": msg,
        "is_me_sid": sid
    }, room=room_id)

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
        if not room.last_word:
            room.last_word = "apple"
            info = await fetch_word_details_fast("apple")
            room.last_ipa = info["ipa"]
            room.last_meaning = info["meaning"]
            
        await sio.emit("game_started", {"message": f"Trận đấu bắt đầu! Từ khởi đầu là '{room.last_word}'"}, room=room_id)
        await start_turn_timer(room_id)

    room_data = room.to_dict()
    await sio.emit("room_updated", room_data, room=room_id)

@sio.event
async def submit_word(sid, data):
    room_id = str(data.get("room_id", "")).strip()
    word = str(data.get("word", "")).strip().lower()

    room = rooms_db.get(room_id)
    if not room or room.status != "PLAYING":
        return

    session = await sio.get_session(sid)
    username = session.get("username", "Người chơi")

    # 1. Kiểm tra tính hợp lệ
    valid, msg = room.validate_and_apply_word(sid, word)
    if not valid:
        await sio.emit("error_message", {"message": msg}, to=sid)
        return

    # 2. Lấy đồng thời IPA và Nghĩa (từ cache hoặc API)
    info = await fetch_word_details_fast(word)
    room.last_ipa = info["ipa"]
    room.last_meaning = info["meaning"]

    # 3. Phát dữ liệu đồng thời cho cả phòng
    await sio.emit("word_accepted", {
        "username": username,
        "word": word,
        "last_word": room.last_word,
        "ipa": info["ipa"],
        "meaning": info["meaning"]
    }, room=room_id)

    # 4. Cập nhật phòng và kích hoạt timer cho người tiếp theo
    await sio.emit("room_updated", room.to_dict(), room=room_id)
    await start_turn_timer(room_id)

    # 5. Lưu điểm vào SQLite
    try:
        with SessionLocal() as db:
            db_user = db.query(UserModel).filter(UserModel.username == username).first()
            if db_user:
                db_user.score += 10
                db.commit()
    except Exception:
        pass

@sio.event
async def disconnect(sid):
    for r_id, room in list(rooms_db.items()):
        if room.get_player(sid):
            room.remove_player(sid)
            await sio.leave_room(sid, r_id)
            
            if not room.players:
                if r_id in room_timers and not room_timers[r_id].done():
                    room_timers[r_id].cancel()
                    del room_timers[r_id]
                del rooms_db[r_id]
            else:
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

# Mount Socket.IO và FastAPI app
combined_app = socketio.ASGIApp(sio, app)

if __name__ == "__main__":
    uvicorn.run("main:combined_app", host="127.0.0.1", port=8000, reload=True)