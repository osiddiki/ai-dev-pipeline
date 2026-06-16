import os
import time
import json
import subprocess
from pathlib import Path

import chromadb
import structlog
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

logger = structlog.get_logger()


class LiteLLMEmbeddingFunction(EmbeddingFunction):
    def __init__(self, model_id: str = "gemini/gemini-embedding-2"):
        self.model_id = model_id

    def __call__(self, input: Documents) -> Embeddings:
        import litellm

        response = litellm.embedding(model=self.model_id, input=input)
        return [data["embedding"] for data in response.data]


class SentenceTransformerEmbeddingFunction(EmbeddingFunction):
    def __init__(self, model_id: str = "BAAI/bge-small-en-v1.5"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "Local semantic search requested but sentence-transformers is not installed."
            ) from exc

        self.model_id = model_id
        self.model = SentenceTransformer(model_id)

    def __call__(self, input: Documents) -> Embeddings:
        vectors = self.model.encode(list(input), normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]


class CodebaseRAG:
    def __init__(
        self,
        repo_path: str,
        provider: str | None = None,
        model_id: str | None = None,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.db_path = self.repo_path / ".gate_rag_cache"
        self.state_path = self.db_path / "index_state.json"
        self.provider = (provider or os.environ.get("GATE_RAG_PROVIDER", "local")).strip().lower()
        self.model_id = model_id or os.environ.get("GATE_RAG_MODEL", "BAAI/bge-small-en-v1.5")
        self._client = None
        self._collection = None
        self._built = False
        self._build_failed = False

    def _get_embedding_function(self) -> EmbeddingFunction:
        if self.provider in {"disabled", "off", "none"}:
            raise RuntimeError("Semantic search is disabled.")
        if self.provider == "local":
            return SentenceTransformerEmbeddingFunction(self.model_id)
        if self.provider == "api":
            return LiteLLMEmbeddingFunction(self.model_id)
        raise RuntimeError(f"Unknown semantic search provider: {self.provider}")

    def _collection_handle(self):
        if self._collection is not None:
            return self._collection

        embedding_function = self._get_embedding_function()
        self._client = chromadb.PersistentClient(path=str(self.db_path))
        self._collection = self._client.get_or_create_collection(
            name="codebase",
            embedding_function=embedding_function,
        )
        return self._collection

    def _get_files(self) -> list[Path]:
        files = []
        for root, dirs, filenames in os.walk(self.repo_path):
            dirs[:] = [
                d
                for d in dirs
                if d
                not in {
                    ".git",
                    "node_modules",
                    "dist",
                    "build",
                    ".gate_rag_cache",
                    "__pycache__",
                    ".venv",
                    "venv",
                    "env",
                }
            ]
            for name in filenames:
                if name.startswith(".") or name.endswith((".pyc", ".png", ".jpg", ".pdf")):
                    continue
                files.append(Path(root) / name)
        return files

    def _current_repo_commit(self) -> str | None:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(self.repo_path),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        commit = result.stdout.strip()
        return commit or None

    def _load_index_state(self) -> dict[str, str]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _is_index_fresh(self) -> bool:
        current_commit = self._current_repo_commit()
        if not current_commit:
            return False

        state = self._load_index_state()
        return (
            state.get("commit") == current_commit
            and state.get("provider") == self.provider
            and state.get("model_id") == self.model_id
        )

    def _write_index_state(self) -> None:
        current_commit = self._current_repo_commit()
        if not current_commit:
            return
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "commit": current_commit,
                    "provider": self.provider,
                    "model_id": self.model_id,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def build_index(self):
        if self._built or self._build_failed:
            return

        if self.provider in {"disabled", "off", "none"}:
            logger.info("Semantic search disabled; skipping index build.")
            self._build_failed = True
            return

        if self._is_index_fresh():
            logger.info("Semantic code index cache is fresh; skipping rebuild.", provider=self.provider, model=self.model_id)
            self._built = True
            return

        logger.info("Building semantic code index", provider=self.provider, model=self.model_id)
        try:
            collection = self._collection_handle()
        except Exception as exc:
            logger.warning("Semantic search unavailable; continuing without it.", error=str(exc))
            self._build_failed = True
            return

        files = self._get_files()
        docs = []
        ids = []
        metadatas = []

        for file_path in files:
            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception:
                continue

            lines = content.split("\n")
            chunk_size = 100
            rel_path = str(file_path.relative_to(self.repo_path))
            for start in range(0, len(lines), chunk_size):
                chunk = "\n".join(lines[start : start + chunk_size])
                if not chunk.strip():
                    continue
                docs.append(f"File: {rel_path}\nLine: {start}\n\n{chunk}")
                ids.append(f"{rel_path}_{start}")
                metadatas.append({"path": rel_path, "start_line": start})

        if not docs:
            self._built = True
            return

        batch_size = 50
        for index in range(0, len(docs), batch_size):
            collection.upsert(
                documents=docs[index : index + batch_size],
                ids=ids[index : index + batch_size],
                metadatas=metadatas[index : index + batch_size],
            )
            if self.provider == "api":
                time.sleep(1)

        self._built = True
        self._write_index_state()
        logger.info("Semantic code index built", chunks=len(docs), provider=self.provider)

    def search(self, query: str, top_k: int = 5) -> str:
        if self.provider in {"disabled", "off", "none"}:
            return (
                "Semantic search is disabled. Set GATE_RAG_PROVIDER=local to use a local embedding model "
                "or GATE_RAG_PROVIDER=api to use a hosted embedding model."
            )

        if not self._built and not self._build_failed:
            self.build_index()
        if self._build_failed:
            return "Semantic search is unavailable in the current environment."

        try:
            results = self._collection_handle().query(query_texts=[query], n_results=top_k)
            if not results["documents"] or not results["documents"][0]:
                return f"No semantic matches found for '{query}'"
            return "\n\n--- SEMANTIC SEARCH RESULT ---\n\n".join(results["documents"][0])
        except Exception as exc:
            return f"Error searching semantic index: {exc}"
