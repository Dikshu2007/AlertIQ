from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User
from app.schemas import TokenOut, UserCreate, UserLogin
from app.security import create_access_token, hash_password, rate_limit, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: UserCreate, request: Request, db: Session = Depends(get_db)):
    rate_limit(request.client.host if request.client else "unknown", limit=settings.rate_limit_per_minute)
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(email=body.email.lower(), password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenOut(access_token=create_access_token(user.id))


@router.post("/login", response_model=TokenOut)
def login(body: UserLogin, request: Request, db: Session = Depends(get_db)):
    rate_limit(request.client.host if request.client else "unknown", limit=settings.rate_limit_per_minute)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenOut(access_token=create_access_token(user.id))
