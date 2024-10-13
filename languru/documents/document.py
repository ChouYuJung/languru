import hashlib
import os
import time
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Optional, Text, Type

from cyksuid.v2 import ksuid
from diskcache import Cache
from openai import OpenAI
from pathvalidate import sanitize_filepath
from pydantic import BaseModel, ConfigDict, Field

from languru.documents._client import (
    DocumentQuerySet,
    DocumentQuerySetDescriptor,
    PointQuerySet,
    PointQuerySetDescriptor,
)
from languru.utils.openai_utils import embeddings_create_with_cache

if TYPE_CHECKING:
    import duckdb


class Point(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    TABLE_NAME: ClassVar[Text] = "points"
    EMBEDDING_MODEL: ClassVar[Text] = "text-embedding-3-small"
    EMBEDDING_DIMENSIONS: ClassVar[int] = 512
    EMBEDDING_CACHE_PATH: ClassVar[Text] = os.path.join(
        os.path.expanduser("~"), ".languru"
    )
    EMBEDDING_CACHE_LIMIT: ClassVar[int] = 2**30  # 1GB
    objects: ClassVar["PointQuerySetDescriptor"] = PointQuerySetDescriptor()

    point_id: Text = Field(
        default_factory=lambda: f"pt_{str(ksuid())}",
        description="The unique and primary ID of the point.",
    )
    document_id: Text = Field(
        description="The ID of the document that the point belongs to."
    )
    content_md5: Text = Field(
        description="The MD5 hash of the document that the point belongs to."
    )
    embedding: List[float] = Field(
        max_length=512,
        description="The embedding of the point.",
        default_factory=list,
    )

    @classmethod
    def query_set(cls) -> "PointQuerySet":
        from languru.documents._client import PointQuerySet

        return PointQuerySet(cls)

    @classmethod
    def embedding_cache(cls, model: Text) -> Cache:
        cache_path = sanitize_filepath(os.path.join(cls.EMBEDDING_CACHE_PATH, model))
        return Cache(directory=cache_path, size_limit=cls.EMBEDDING_CACHE_LIMIT)

    def is_embedded(self) -> bool:
        return False if len(self.embedding) == 0 else True


class Document(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    TABLE_NAME: ClassVar[Text] = "documents"
    POINT_TYPE: ClassVar[Type[Point]] = Point
    objects: ClassVar["DocumentQuerySetDescriptor"] = DocumentQuerySetDescriptor()

    document_id: Text = Field(
        default_factory=lambda: f"doc_{str(ksuid())}",
        description="The unique and primary ID of the document.",
    )
    name: Text = Field(max_length=255, description="The unique name of the document.")
    content: Text = Field(max_length=5000, description="The content of the document.")
    content_md5: Text = Field(description="The MD5 hash of the document content.")
    metadata: Dict[Text, Any] = Field(
        default_factory=dict, description="Any additional metadata for the document."
    )
    created_at: int = Field(
        default_factory=lambda: int(time.time()),
        description="The timestamp of when the document was created.",
    )
    updated_at: int = Field(
        default_factory=lambda: int(time.time()),
        description="The timestamp of when the document was last updated.",
    )

    @classmethod
    def query_set(cls) -> "DocumentQuerySet":
        from languru.documents._client import DocumentQuerySet

        return DocumentQuerySet(cls)

    @classmethod
    def from_content(
        cls, name: Text, content: Text, *, metadata: Optional[Dict[Text, Any]] = None
    ) -> "Document":
        return cls.model_validate(
            {
                "content": content,
                "content_md5": cls.hash_content(content),
                "name": name,
                "metadata": metadata or {},
            }
        )

    @classmethod
    def hash_content(cls, content: Text) -> Text:
        return hashlib.md5(content.strip().encode("utf-8")).hexdigest()

    def to_points(
        self,
        *,
        openai_client: Optional["OpenAI"] = None,
        embedding: Optional[List[float]] = None,
    ) -> List["Point"]:
        self.strip()

        params: Dict = {
            "document_id": self.document_id,
            "content_md5": self.content_md5,
        }

        if embedding is not None:
            params["embedding"] = embedding
        elif openai_client is not None:
            embeddings = embeddings_create_with_cache(
                input=self.to_embedding_contents(),
                model=self.POINT_TYPE.EMBEDDING_MODEL,
                dimensions=self.POINT_TYPE.EMBEDDING_DIMENSIONS,
                openai_client=openai_client,
                cache=self.POINT_TYPE.embedding_cache(self.POINT_TYPE.EMBEDDING_MODEL),
            )
            params["embedding"] = embeddings[0]

        point_out = self.POINT_TYPE.model_validate(params)

        return [point_out]

    def has_points(self, conn: "duckdb.DuckDBPyConnection") -> bool:
        return (
            len(
                self.POINT_TYPE.objects.list(
                    document_id=self.document_id, conn=conn, limit=1
                ).data
            )
            > 0
        )

    def are_points_current(
        self, conn: "duckdb.DuckDBPyConnection", *, debug: bool = False
    ) -> bool:
        after = None
        has_more = True
        while has_more:
            page_points = Point.objects.list(
                document_id=self.document_id, after=after, conn=conn, debug=debug
            )

            # Return False if no points are found
            if len(page_points.data) == 0 and after is None:
                return False

            # Return False if any point is out of date
            for pt in page_points.data:
                if pt.content_md5 != self.content_md5:
                    return False

            # Update pagination state
            after = page_points.last_id
            has_more = page_points.has_more

        return True

    def refresh_points(self, conn: "duckdb.DuckDBPyConnection") -> None:
        pass

    def to_embedding_contents(self, *args, **kwargs) -> List[Text]:
        return [self.content.strip()]

    def strip(self, *, copy: bool = False) -> "Document":
        _doc = self.model_copy(deep=True) if copy else self
        _doc.content = _doc.content.strip()
        new_md5 = self.hash_content(_doc.content)
        if _doc.content_md5 != new_md5:
            _doc.content_md5 = new_md5
            _doc.updated_at = int(time.time())
        return _doc
