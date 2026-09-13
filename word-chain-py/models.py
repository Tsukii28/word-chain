from pydantic import BaseModel, Field
from typing import List, Optional

# --- Schemas Auth ---
class UserRegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=20, example="player_one")
    password: str = Field(..., min_length=6, example="secret123")

class UserLoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str

class UserProfileResponse(BaseModel):
    id: str
    username: str
    score: int
    games_played: int
    games_won: int

# --- Schemas Room ---
class CreateRoomRequest(BaseModel):
    room_name: str = Field(..., min_length=3, max_length=30, example="Room Cao Thủ")
    max_players: int = Field(default=4, ge=2, le=8)
    turn_time_seconds: int = Field(default=15, ge=5, le=60)

class RoomResponse(BaseModel):
    room_id: str
    room_name: str
    status: str
    player_count: int
    max_players: int
    turn_time_seconds: int