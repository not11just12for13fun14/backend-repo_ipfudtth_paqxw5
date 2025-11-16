import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext

from pydantic import BaseModel

from database import db, create_document, get_documents
from schemas import User, Test, Question, Package, Assignment, Attempt, Message

# Security settings
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-key-change")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 12

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

app = FastAPI(title="LMS Testing Portal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------- Auth Helpers ----------------------
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    user_id: Optional[str] = None
    role: Optional[str] = None


def verify_password(plain_password, password_hash):
    return pwd_context.verify(plain_password, password_hash)


def get_password_hash(password):
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(status_code=401, detail="Could not validate credentials")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        role: str = payload.get("role")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.user.find_one({"_id": db.client.get_default_database().codec_options.document_class.object_hook if False else {}})
    # Simple fetch by _id string
    from bson import ObjectId
    doc = db.user.find_one({"_id": ObjectId(user_id)})
    if not doc:
        raise credentials_exception
    doc["_id"] = str(doc["_id"])  # normalize
    return doc


def require_role(required: List[str]):
    async def role_dep(user = Depends(get_current_user)):
        if user.get("role") not in required:
            raise HTTPException(403, detail="Insufficient permissions")
        return user
    return role_dep


# ---------------------- Basic Routes ----------------------
@app.get("/")
def read_root():
    return {"message": "LMS Testing Portal API running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name
            response["connection_status"] = "Connected"
            response["collections"] = db.list_collection_names()[:10]
            response["database"] = "✅ Connected & Working"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response


# ---------------------- Auth Endpoints ----------------------
@app.post("/auth/register", response_model=Token)
async def register(name: str = Form(...), email: str = Form(...), password: str = Form(...), role: str = Form("student")):
    # Prevent duplicate
    if db.user.find_one({"email": email}):
        raise HTTPException(400, detail="Email already registered")
    uid = create_document("user", User(name=name, email=email, password_hash=get_password_hash(password), role=role))
    token = create_access_token({"sub": uid, "role": role})
    return Token(access_token=token)


@app.post("/auth/token", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = db.user.find_one({"email": form_data.username})
    if not user or not verify_password(form_data.password, user.get("password_hash", "")):
        raise HTTPException(401, detail="Incorrect email or password")
    token = create_access_token({"sub": str(user["_id"]), "role": user.get("role", "student")})
    return Token(access_token=token)


@app.get("/auth/me")
async def me(user = Depends(get_current_user)):
    return {k: v for k, v in user.items() if k != "password_hash"}


# ---------------------- Admin: Tests & Questions ----------------------
@app.post("/admin/tests", dependencies=[Depends(require_role(["admin"]))])
async def create_test(payload: Test):
    tid = create_document("test", payload)
    return {"id": tid}


@app.get("/admin/tests", dependencies=[Depends(require_role(["admin", "teacher"]))])
async def list_tests():
    tests = get_documents("test")
    for t in tests:
        t["_id"] = str(t["_id"])
    return tests


@app.post("/admin/questions/upload", dependencies=[Depends(require_role(["admin"]))])
async def upload_questions(
    test_id: str = Form(...),
    section: str = Form(...),
    module: int = Form(...),
    file: UploadFile = File(...)
):
    # Expect CSV with columns: number,type,prompt,choices,correct,difficulty,topic,explanation
    import csv, io, json
    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode()))
    inserted = 0
    for row in reader:
        choices = None
        if row.get("choices"):
            try:
                choices = json.loads(row["choices"]) if row["choices"].strip().startswith("[") else [c.strip() for c in row["choices"].split("||")]
            except Exception:
                choices = [c.strip() for c in row["choices"].split("||")]
        qdoc = Question(
            test_id=test_id,
            section=section,
            module=int(module),
            number=int(row.get("number", 0)),
            type=row.get("type", "mcq"),
            prompt=row.get("prompt", ""),
            choices=choices,
            correct=row.get("correct"),
            difficulty=row.get("difficulty", "medium"),
            topic=row.get("topic"),
            explanation=row.get("explanation"),
        )
        create_document("question", qdoc)
        inserted += 1
    return {"inserted": inserted}


@app.post("/admin/assign", dependencies=[Depends(require_role(["admin", "teacher"]))])
async def assign_test(assignment: Assignment):
    aid = create_document("assignment", assignment)
    return {"id": aid}


# ---------------------- Student: Assignments, Attempt Engine ----------------------
@app.get("/student/assignments", dependencies=[Depends(require_role(["student"]))])
async def my_assignments(user = Depends(get_current_user)):
    docs = get_documents("assignment", {"student_id": str(user["_id"])})
    for d in docs:
        d["_id"] = str(d["_id"])
    return docs


@app.post("/student/attempt/start", dependencies=[Depends(require_role(["student"]))])
async def start_attempt(test_id: str = Form(...), user = Depends(get_current_user)):
    # Load structure to set timers
    t = db.test.find_one({"_id": db.test._Database__client.get_default_database if False else {}})
    from bson import ObjectId
    test_doc = db.test.find_one({"_id": ObjectId(test_id)})
    if not test_doc:
        raise HTTPException(404, detail="Test not found")
    structure = test_doc.get("structure", {
        "sections": {
            "RW": {"modules": [{"duration": 1920}, {"duration": 1920}]},
            "Math": {"modules": [{"duration": 2100}, {"duration": 2100}]}
        }
    })
    timers = {
        f"RW-1": structure["sections"]["RW"]["modules"][0].get("duration", 1920),
        f"RW-2": structure["sections"]["RW"]["modules"][1].get("duration", 1920),
        f"Math-1": structure["sections"]["Math"]["modules"][0].get("duration", 2100),
        f"Math-2": structure["sections"]["Math"]["modules"][1].get("duration", 2100),
    }
    attempt = Attempt(student_id=str(user["_id"]), test_id=test_id, timers=timers)
    aid = create_document("attempt", attempt)
    return {"attempt_id": aid, "timers": timers}


class AnswerPayload(BaseModel):
    attempt_id: str
    qid: str
    answer: Any
    time_spent: int


@app.post("/student/attempt/answer", dependencies=[Depends(require_role(["student"]))])
async def save_answer(payload: AnswerPayload, user = Depends(get_current_user)):
    from bson import ObjectId
    att = db.attempt.find_one({"_id": ObjectId(payload.attempt_id), "student_id": str(user["_id"])})
    if not att:
        raise HTTPException(404, detail="Attempt not found")
    db.attempt.update_one(
        {"_id": ObjectId(payload.attempt_id)},
        {"$set": {f"answers.{payload.qid}": payload.answer, f"time_spent.{payload.qid}": payload.time_spent, "updated_at": datetime.now(timezone.utc)}}
    )
    return {"status": "saved"}


@app.post("/student/attempt/submit", dependencies=[Depends(require_role(["student"]))])
async def submit_attempt(attempt_id: str = Form(...), user = Depends(get_current_user)):
    from bson import ObjectId
    att = db.attempt.find_one({"_id": ObjectId(attempt_id), "student_id": str(user["_id"])})
    if not att:
        raise HTTPException(404, detail="Attempt not found")
    # Simple scoring: +1 for each correct
    total = 0
    correct = 0
    for qid, ans in (att.get("answers") or {}).items():
        q = db.question.find_one({"_id": ObjectId(qid)})
        if not q:
            continue
        total += 1
        if str(ans).strip() == str(q.get("correct")).strip():
            correct += 1
    score = {
        "raw": correct,
        "total": total,
        "percent": round((correct / total) * 100, 2) if total else 0.0
    }
    db.attempt.update_one({"_id": ObjectId(attempt_id)}, {"$set": {"status": "submitted", "submitted_at": datetime.now(timezone.utc), "score": score}})
    return {"score": score}


# ---------------------- Teacher: Students & Reports ----------------------
@app.get("/teacher/students", dependencies=[Depends(require_role(["teacher"]))])
async def teacher_students(user = Depends(get_current_user)):
    docs = list(db.user.find({"teacher_id": str(user["_id"])}))
    for d in docs:
        d["_id"] = str(d["_id"])  # normalize
        d.pop("password_hash", None)
    return docs


@app.get("/teacher/reports/{student_id}", dependencies=[Depends(require_role(["teacher"]))])
async def teacher_report(student_id: str, user = Depends(get_current_user)):
    # basic roll-up
    attempts = list(db.attempt.find({"student_id": student_id}))
    for a in attempts:
        a["_id"] = str(a["_id"])  # normalize
    summary = {
        "count": len(attempts),
        "average_percent": round(sum(a.get("score", {}).get("percent", 0) for a in attempts) / len(attempts), 2) if attempts else 0,
    }
    return {"summary": summary, "attempts": attempts}


# ---------------------- Messaging ----------------------
@app.post("/messages", dependencies=[Depends(require_role(["teacher", "student"]))])
async def send_message(payload: Message, user = Depends(get_current_user)):
    mid = create_document("message", payload)
    return {"id": mid}


@app.get("/messages", dependencies=[Depends(require_role(["teacher", "student"]))])
async def list_messages(user = Depends(get_current_user)):
    msgs = get_documents("message", {"$or": [{"sender_id": str(user["_id"])}, {"recipient_id": str(user["_id"])}]})
    for m in msgs:
        m["_id"] = str(m["_id"])  # normalize
    return msgs


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
