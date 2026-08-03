from datetime import datetime

from pydantic import BaseModel, Field


class DocumentResponse(BaseModel):
    id: str
    filename: str
    file_type: str
    status: str
    chunk_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentUploadResponse(BaseModel):
    document_id: str
    status: str = "processing"
    message: str = "文档已接收，正在后台处理"


class DocumentSearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20)


class DocumentSearchResult(BaseModel):
    content: str
    source: str
    score: float
