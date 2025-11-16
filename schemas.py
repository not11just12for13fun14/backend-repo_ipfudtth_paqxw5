"""
SAT/ACT LMS Portal Schemas

Each Pydantic model below maps to a MongoDB collection. The collection name is the lowercase of the class name.

Collections:
- user: accounts for admin/teacher/student
- test: SAT/ACT tests with sections and modules metadata
- question: bank of questions linked to a test and module
- package: bundles of tests
- assignment: assignments of tests/packages to students (optionally by teacher)
- attempt: student test attempts, module timing, answers, and scores
- message: simple teacher-student notes/messages
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict, Any

Role = Literal["admin", "teacher", "student"]
ExamType = Literal["SAT", "ACT"]
QuestionType = Literal["mcq", "multi", "gridin"]

class User(BaseModel):
    name: str
    email: str
    password_hash: str
    role: Role = "student"
    is_active: bool = True
    teacher_id: Optional[str] = Field(None, description="If student is assigned to a teacher")
    avatar_url: Optional[str] = None

class Test(BaseModel):
    title: str
    exam_type: ExamType = "SAT"
    description: Optional[str] = None
    # SAT: 2 sections (RW, Math), each 2 modules
    structure: Dict[str, Any] = Field(
        ..., description="Exam structure definition including sections and module order"
    )
    # convenience flags
    is_published: bool = False
    tags: List[str] = []

class Question(BaseModel):
    test_id: str
    section: Literal["RW", "Math"]
    module: Literal[1, 2]
    number: int
    type: QuestionType
    prompt: str
    passage: Optional[str] = None
    image_url: Optional[str] = None
    choices: Optional[List[str]] = None
    correct: Any = Field(..., description="Correct answer: index for mcq, list of indices for multi, string/number for gridin")
    difficulty: Optional[Literal["easy", "medium", "hard"]] = "medium"
    topic: Optional[str] = None
    explanation: Optional[str] = None

class Package(BaseModel):
    name: str
    description: Optional[str] = None
    test_ids: List[str] = []
    tier: Optional[str] = None  # e.g., Basic, Premium

class Assignment(BaseModel):
    student_id: str
    assigned_by: Optional[str] = None  # admin/teacher id
    test_id: Optional[str] = None
    package_id: Optional[str] = None
    due_date: Optional[str] = None
    status: Literal["assigned", "in_progress", "completed"] = "assigned"

class Attempt(BaseModel):
    student_id: str
    test_id: str
    status: Literal["in_progress", "submitted"] = "in_progress"
    started_at: Optional[str] = None
    submitted_at: Optional[str] = None
    # time per module in seconds (SAT typical 32 min RW module, 35 min Math etc. configurable from Test.structure)
    timers: Dict[str, int] = {}
    # answers keyed by qid -> answer
    answers: Dict[str, Any] = {}
    # computed after submit
    score: Optional[Dict[str, Any]] = None
    time_spent: Dict[str, int] = {}  # qid -> seconds

class Message(BaseModel):
    sender_id: str
    recipient_id: str
    text: str
    related_student_id: Optional[str] = None
    related_attempt_id: Optional[str] = None
    is_read: bool = False
