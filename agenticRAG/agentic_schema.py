from typing import Annotated, Any, Dict, List, Literal, TypedDict

from pydantic import BaseModel, Field

PLAN_MIN_ITEMS = 1
PLAN_MAX_ITEMS = 5
QueryMode = Literal["local", "global", "hybrid"]


class SubQuestionQueryPlan(BaseModel):
    sub_questions: Annotated[
        List[str],
        Field(
            min_length=PLAN_MIN_ITEMS,
            max_length=PLAN_MAX_ITEMS,
            description="把用户问题拆成1到5个更具体、互不重复、适合直接检索的子问题",
        ),
    ]
    query_modes: Annotated[
        List[QueryMode],
        Field(
            min_length=PLAN_MIN_ITEMS,
            max_length=PLAN_MAX_ITEMS,
            description="子问题对应的查询模式",
        ),
    ]
    query_topks: Annotated[
        List[int],
        Field(
            min_length=PLAN_MIN_ITEMS,
            max_length=PLAN_MAX_ITEMS,
            description="子问题对应的 top_k",
        ),
    ]
    query_chunk_topks: Annotated[
        List[int],
        Field(
            min_length=PLAN_MIN_ITEMS,
            max_length=PLAN_MAX_ITEMS,
            description="子问题对应的 chunk_top_k",
        ),
    ]


class QuestionComplexity(BaseModel):
    complexity: Literal["simple", "complex"] = Field(
        description="问题复杂度分类:simple 表示可直接检索回答,complex 表示需要拆分多步检索",
    )
    reason: str = Field(description="分类理由，简短即可")


class EvidenceCheck(BaseModel):
    sufficient: bool = Field(description="当前检索结果是否足以回答该子问题")
    reason: str = Field(description="判断理由，简短即可")


class SubQuestionRewrite(BaseModel):
    rewritten_question: str = Field(
        description="针对证据不足的子问题，给出一个更具体、更容易检索到答案的改写问题",
    )
    reason: str = Field(
        default="",
        description="改写理由，简短说明改写如何补足检索缺口",
    )


class State(TypedDict, total=False):
    question: str
    requested_mode: str
    detected_complexity: str
    question_complexity: str
    effective_strategy: str
    planning_reason: str
    sub_questions: List[Any]
    subquery_tasks: List[Dict[str, Any]]
    subquery_results: List[Dict[str, str]]
    allowed_subject_ids: List[str]
    subject_working_dirs: Dict[str, str]
    response_language: str
    query_attempt: int
    needs_retry: bool
    insufficient_subquestion_ids: List[str]
    query_total_ms: str
    final_answer: str
    answer_style_instruction: str
    retry_rewrites: List[Dict[str, Any]]
