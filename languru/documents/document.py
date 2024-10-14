import hashlib
import json
import os
import time
from textwrap import dedent
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Optional, Text, Tuple, Type

import jinja2
from cyksuid.v2 import ksuid
from diskcache import Cache
from openai import OpenAI
from pathvalidate import sanitize_filepath
from pydantic import BaseModel, ConfigDict, Field

from languru.config import console
from languru.documents._client import (
    DocumentQuerySet,
    DocumentQuerySetDescriptor,
    PointQuerySet,
    PointQuerySetDescriptor,
)
from languru.utils.openai_utils import embeddings_create_with_cache
from languru.utils.sql import (
    DISPLAY_SQL_PARAMS,
    DISPLAY_SQL_QUERY,
    display_sql_parameters,
)

if TYPE_CHECKING:
    import duckdb
    import pandas as pd


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

        return PointQuerySet(cls, model_with_score=PointWithScore)

    @classmethod
    def embedding_cache(cls, model: Text) -> Cache:
        cache_path = sanitize_filepath(os.path.join(cls.EMBEDDING_CACHE_PATH, model))
        return Cache(directory=cache_path, size_limit=cls.EMBEDDING_CACHE_LIMIT)

    def is_embedded(self) -> bool:
        return False if len(self.embedding) == 0 else True


class PointWithScore(Point):
    model_config = ConfigDict(str_strip_whitespace=True, extra="allow")

    relevance_score: float = Field(description="The score of the point.")


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

    @classmethod
    def search(
        cls, query: Text, *, conn: "duckdb.DuckDBPyConnection", openai_client: "OpenAI"
    ) -> "SearchResult":
        vectors = embeddings_create_with_cache(
            input=cls.to_query_cards(query),
            model=cls.POINT_TYPE.EMBEDDING_MODEL,
            dimensions=cls.POINT_TYPE.EMBEDDING_DIMENSIONS,
            openai_client=openai_client,
            cache=cls.POINT_TYPE.embedding_cache(cls.POINT_TYPE.EMBEDDING_MODEL),
        )

    @classmethod
    def search_single_vector(
        cls,
        vector: List[float],
        *,
        conn: "duckdb.DuckDBPyConnection",
        top_k: int = 100,
        with_embedding: bool = False,
        with_documents: bool = False,
        debug: bool = False,
    ) -> Tuple[List[PointWithScore], Optional[List["Document"]]]:
        time_start = time.perf_counter() if debug else None

        # Get point columns
        point_columns = list(cls.POINT_TYPE.model_json_schema()["properties"].keys())
        if not with_embedding:
            point_columns = [c for c in point_columns if c != "embedding"]
        point_columns += [
            "array_cosine_similarity("
            + f"embedding, ?::FLOAT[{cls.POINT_TYPE.EMBEDDING_DIMENSIONS}]"
            + ") AS relevance_score"
        ]
        point_columns_expr = ", ".join(point_columns)

        if with_documents:
            query_template = jinja2.Template(
                dedent(
                    """
                    WITH vector_search AS (
                        SELECT {{ columns_expr }}
                        FROM {{ table_name }}
                        ORDER BY relevance_score DESC
                        LIMIT {{ top_k }}
                    )
                    SELECT p, d
                    FROM vector_search p
                    JOIN {{ document_table_name }} d ON p.document_id = d.document_id
                    """
                ).strip()
            )
            query = query_template.render(
                table_name=cls.POINT_TYPE.TABLE_NAME,
                document_table_name=cls.TABLE_NAME,
                columns_expr=point_columns_expr,
                top_k=top_k,
            )
        else:
            query_template = jinja2.Template(sql_stmt_vector_search)
            query = query_template.render(
                table_name=cls.POINT_TYPE.TABLE_NAME,
                columns_expr=point_columns_expr,
                top_k=top_k,
            )
        parameters = [vector]

        if debug:
            _display_params = display_sql_parameters(parameters)
            console.print(
                "\nVector search with SQL:\n"
                + f"{DISPLAY_SQL_QUERY.format(sql=query.strip())}\n"
                + f"{DISPLAY_SQL_PARAMS.format(params=_display_params)}\n"
            )

        # Execute query
        results_df: "pd.DataFrame" = (
            conn.execute(query, parameters).fetch_arrow_table().to_pandas()
        )

        # Parse results
        points_with_score = []
        documents = []
        if with_documents:
            walked_docs = set()
            rows: List[Dict] = results_df.to_dict(orient="records")
            for row in rows:
                _point_data = row["p"]
                points_with_score.append(PointWithScore.model_validate(_point_data))
                if "d" in row:
                    _doc_data = row["d"]
                    if _doc_data["document_id"] not in walked_docs:
                        if isinstance(_doc_data.get("metadata"), Text):
                            _doc_data["metadata"] = json.loads(_doc_data["metadata"])
                        documents.append(cls.model_validate(_doc_data))
                        walked_docs.add(_doc_data["document_id"])
        else:
            points_with_score = [
                PointWithScore.model_validate(row)
                for row in results_df.to_dict(orient="records")
            ]

        # Log execution time
        if time_start is not None:
            time_end = time.perf_counter()
            time_elapsed = (time_end - time_start) * 1000
            console.print(f"Vector search execution time: {time_elapsed:.2f} ms")

        return (points_with_score, documents)

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
                input=self.to_document_cards(),
                model=self.POINT_TYPE.EMBEDDING_MODEL,
                dimensions=self.POINT_TYPE.EMBEDDING_DIMENSIONS,
                openai_client=openai_client,
                cache=self.POINT_TYPE.embedding_cache(self.POINT_TYPE.EMBEDDING_MODEL),
            )
            params["embedding"] = embeddings[0]

        point_out = self.POINT_TYPE.model_validate(params)

        return [point_out]

    def has_points(
        self, *, conn: "duckdb.DuckDBPyConnection", debug: bool = False
    ) -> bool:
        return (
            self.POINT_TYPE.objects.count(
                document_id=self.document_id, conn=conn, debug=debug
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

    def to_document_cards(self, *args, **kwargs) -> List[Text]:
        return [self.content.strip()]

    @classmethod
    def to_query_cards(cls, query: Text, *args, **kwargs) -> List[Text]:
        return [query.strip()]

    def strip(self, *, copy: bool = False) -> "Document":
        _doc = self.model_copy(deep=True) if copy else self
        _doc.content = _doc.content.strip()
        new_md5 = self.hash_content(_doc.content)
        if _doc.content_md5 != new_md5:
            _doc.content_md5 = new_md5
            _doc.updated_at = int(time.time())
        return _doc


class SearchResult(BaseModel):
    query: Optional[Text] = Field(
        default=None, description="The query that was used for searching."
    )
    matches: List[PointWithScore] = Field(
        default_factory=list, description="The points that match the search query."
    )
    documents: List[Document] = Field(
        default_factory=list, description="The documents that match the search query."
    )
    total_results: int = Field(
        default=0, description="The total number of results found."
    )
    execution_time: float = Field(
        default=0.0,
        description="The time taken to execute the search query in seconds.",
    )
    relevance_score: Optional[float] = Field(
        default=None, description="An overall relevance score for the search result."
    )
    highlight: Optional[Dict[Text, List[Text]]] = Field(
        default=None,
        description="Highlighted snippets of text that match the search query.",
    )
    facets: Optional[Dict[Text, Dict[Text, int]]] = Field(
        default=None,
        description="Faceted search results for categorization.",
    )
    suggestions: Optional[List[Text]] = Field(
        default=None, description="Suggested search terms related to the query."
    )


sql_stmt_vector_search = dedent(
    """
    SELECT {{ columns_expr }}
    FROM {{ table_name }}
    ORDER BY relevance_score DESC
    LIMIT {{ top_k }}
    """
).strip()
sql_stmt_vector_search_with_documents = dedent(
    """
    WITH vector_search AS (
    SELECT {{ columns_expr }}
    FROM {{ table_name }}
    ORDER BY relevance_score DESC
    LIMIT {{ top_k }}
    )
    SELECT p, d
    FROM vector_search p
    JOIN {{ document_table_name }} d ON p.document_id = d.document_id
    """
).strip()
