from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ImageEvidence(StrictModel):
    image_ref: str
    ocr_original: str
    ocr_zh: str
    description_zh: str
    confidence: float = Field(ge=0, le=1)


class VisualEvidence(StrictModel):
    images: list[ImageEvidence]


class Transcript(StrictModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text_original: str
    text_zh: str


class OCR(StrictModel):
    image_ref: str
    text_original: str
    text_zh: str
    confidence: float = Field(ge=0, le=1)


class Visual(StrictModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    frame_ref: str
    description_zh: str


class Item(StrictModel):
    type: Literal['knowledge', 'method', 'opinion', 'style', 'step']
    title: str
    content_zh: str
    content_original: str
    source_segment_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    claim_kind: Literal['source_claim', 'inference']


class KnowledgeOutput(StrictModel):
    schema_version: Literal['knowledge_v1']
    platform: str
    work_id: str
    source_url: str
    title_original: str
    summary_zh: str
    language_original: str
    transcript_segments: list[Transcript]
    ocr_segments: list[OCR]
    visual_segments: list[Visual]
    knowledge_items: list[Item] = Field(min_length=1)
    creator_thoughts: list[str]
    expression_style: list[str]
    action_steps: list[str]
    cross_creator_candidates: list[str]
    limitations: list[str]


class CreatorInput(StrictModel):
    profile_url: str = Field(max_length=2000)
    display_name: str = Field(default='', max_length=200)
    platform: Literal['douyin', 'xiaohongshu'] | None = None


class Question(StrictModel):
    question: str = Field(min_length=1, max_length=4000)
    filters: dict = Field(default_factory=dict)


class Comparison(Question):
    creator_ids: list[str] = Field(min_length=2, max_length=10)
