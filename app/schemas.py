from pydantic import BaseModel, Field

class LessonStepOut(BaseModel):
    step_number: int
    title: str
    instruction: str
    start_seconds: float = 0
    end_seconds: float = 0
    visual_observation: str = ""
    safety: list[str] = []

class LessonOut(BaseModel):
    title: str = "Untitled lesson"
    summary: str = ""
    tools: list[str] = []
    materials: list[str] = []
    safety_warnings: list[str] = []
    steps: list[LessonStepOut] = []

class QuestionIn(BaseModel):
    question: str = Field(min_length=2, max_length=1200)
    current_timestamp: float | None = None
