from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PageFields(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(default="", max_length=1_000_000)
    parent_id: str | None = Field(default=None, max_length=36)
    tags: list[str] = Field(default_factory=list, max_length=24)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Title cannot be blank")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, tags):
        result = []
        for tag in tags:
            tag = tag.strip()
            if not tag or len(tag) > 64:
                raise ValueError("Tags must contain between 1 and 64 characters")
            if tag not in result:
                result.append(tag)
        return result


class PageCreate(PageFields):
    pass


class VersionInput(BaseModel):
    version: int = Field(ge=1)


class PageUpdate(VersionInput):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    content: str | None = Field(default=None, max_length=1_000_000)
    parent_id: str | None = Field(default=None, max_length=36)
    tags: list[str] | None = Field(default=None, max_length=24)

    @model_validator(mode="after")
    def validate_changes(self):
        for field in ("title", "content", "tags"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if self.title is not None:
            self.title = PageFields.title_not_blank(self.title)
        if self.tags is not None:
            self.tags = PageFields.normalize_tags(self.tags)
        return self


class PageOut(PageFields):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    version: int
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str


class RevisionOut(PageFields):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    page_id: str
    version: int
    deleted_at: datetime | None
    created_at: datetime
    created_by: str


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    created_by: str
    status: str
    total: int
    processed: int
    result: list
    error: str | None
    attempts: int
    created_at: datetime
    updated_at: datetime
