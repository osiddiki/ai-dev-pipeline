import os
from pathlib import Path
import chromadb
from chromadb.api.types import EmbeddingFunction, Documents, Embeddings
import structlog

logger = structlog.get_logger()

class LiteLLMEmbeddingFunction(EmbeddingFunction):
    def __init__(self, model_id: str = "gemini/text-embedding-004"):
        self.model_id = model_id

    def __call__(self, input: Documents) -> Embeddings:
        import litellm
        try:
            response = litellm.embedding(model=self.model_id, input=input)
            return [data["embedding"] for data in response.data]
        except Exception as e:
            logger.error("Embedding failed", error=str(e))
            # Fallback to local default if API fails
            from chromadb.utils import embedding_functions
            default_ef = embedding_functions.DefaultEmbeddingFunction()
            return default_ef(input)

class CodebaseRAG:
    def __init__(self, repo_path: str, model_id: str = "gemini/text-embedding-004"):
        self.repo_path = Path(repo_path).resolve()
        self.db_path = self.repo_path / ".gate_rag_cache"
        self.client = chromadb.PersistentClient(path=str(self.db_path))
        self.ef = LiteLLMEmbeddingFunction(model_id=model_id)
        self.collection = self.client.get_or_create_collection(
            name="codebase", 
            embedding_function=self.ef
        )

    def _get_files(self) -> list[Path]:
        files = []
        for root, dirs, filenames in os.walk(self.repo_path):
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "dist", "build", ".gate_rag_cache", "__pycache__"}]
            for name in filenames:
                if name.startswith(".") or name.endswith((".pyc", ".png", ".jpg", ".pdf")):
                    continue
                files.append(Path(root) / name)
        return files

    def build_index(self):
        logger.info("Building RAG index...")
        files = self._get_files()
        
        docs = []
        ids = []
        metadatas = []
        
        for f in files:
            try:
                content = f.read_text(encoding="utf-8")
                lines = content.split('\n')
                chunk_size = 100
                for i in range(0, len(lines), chunk_size):
                    chunk = '\n'.join(lines[i:i+chunk_size])
                    if not chunk.strip():
                        continue
                    
                    rel_path = str(f.relative_to(self.repo_path))
                    chunk_id = f"{rel_path}_{i}"
                    
                    docs.append(f"File: {rel_path}\nLine: {i}\n\n{chunk}")
                    ids.append(chunk_id)
                    metadatas.append({"path": rel_path, "start_line": i})
            except Exception:
                continue

        batch_size = 100
        for i in range(0, len(docs), batch_size):
            try:
                self.collection.upsert(
                    documents=docs[i:i+batch_size],
                    ids=ids[i:i+batch_size],
                    metadatas=metadatas[i:i+batch_size]
                )
            except Exception as e:
                logger.error("RAG upsert batch failed", error=str(e))
                
        logger.info("RAG index built", total_chunks=len(docs))

    def search(self, query: str, top_k: int = 5) -> str:
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=top_k
            )
            if not results["documents"] or not results["documents"][0]:
                return f"No semantic matches found for '{query}'"
                
            out = []
            for doc in results["documents"][0]:
                out.append(doc)
                
            return "\n\n--- SEMANTIC SEARCH RESULT ---\n\n".join(out)
        except Exception as e:
            return f"Error searching RAG: {str(e)}"
