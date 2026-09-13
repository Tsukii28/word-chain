from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker

SQLALCHEMY_DATABASE_URL = "sqlite:///./word_chain.db"

# connect_args={"check_same_thread": False} cần thiết cho SQLite trên FastAPI
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    score = Column(Integer, default=0)
    games_played = Column(Integer, default=0)
    games_won = Column(Integer, default=0)

# Hàm khởi tạo bảng dữ liệu (đảm bảo tên hàm đúng là init_db)
def init_db():
    Base.metadata.create_all(bind=engine)

# Dependency phiên làm việc DB cho FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()