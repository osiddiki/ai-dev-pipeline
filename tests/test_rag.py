import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from environment.rag import CodebaseRAG
from environment.tools import CodebaseTools


class FakeCollection:
    def __init__(self):
        self.upserts = []
        self.queries = []

    def upsert(self, documents, ids, metadatas):
        self.upserts.append(
            {
                "documents": documents,
                "ids": ids,
                "metadatas": metadatas,
            }
        )

    def query(self, query_texts, n_results):
        self.queries.append({"query_texts": query_texts, "n_results": n_results})
        return {"documents": [["File: src/app.ts\nLine: 0\n\nexport const app = true;"]]}


class FakeClient:
    def __init__(self, path):
        self.path = path
        self.collection = FakeCollection()

    def get_or_create_collection(self, name, embedding_function):
        return self.collection


class FakeSentenceTransformerEmbeddingFunction:
    def __init__(self, model_id):
        self.model_id = model_id

    def __call__(self, input_documents):
        return [[0.1, 0.2, 0.3] for _ in input_documents]


class TestCodebaseRAG(unittest.TestCase):
    def test_default_provider_is_local(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rag = CodebaseRAG(tmpdir)

        self.assertEqual(rag.provider, "local")

    def test_disabled_provider_returns_clear_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rag = CodebaseRAG(tmpdir, provider="disabled")
            rag.build_index()
            message = rag.search("where is auth")

        self.assertIn("Semantic search is disabled", message)
        self.assertTrue(rag._build_failed)

    def test_local_provider_builds_index_with_stubbed_dependencies(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            (repo / "src").mkdir()
            (repo / "src" / "app.ts").write_text("export const app = true;\n", encoding="utf-8")
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

            fake_client = FakeClient(str(repo / ".gate_rag_cache"))
            with (
                mock.patch("environment.rag.SentenceTransformerEmbeddingFunction", FakeSentenceTransformerEmbeddingFunction),
                mock.patch("environment.rag.chromadb.PersistentClient", return_value=fake_client),
            ):
                rag = CodebaseRAG(str(repo), provider="local", model_id="fake-local-model")
                rag.build_index()
                result = rag.search("app export")

        self.assertTrue(rag._built)
        self.assertGreaterEqual(len(fake_client.collection.upserts), 1)
        self.assertIn("export const app = true", result)

    def test_codebase_tools_semantic_search_is_lazy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tools = CodebaseTools(tmpdir)
            fake_rag = mock.Mock()
            fake_rag.search.return_value = "semantic-result"
            tools._rag = fake_rag

            result = tools.semantic_code_search("find the login flow")

        fake_rag.build_index.assert_called_once()
        fake_rag.search.assert_called_once_with("find the login flow")
        self.assertEqual(result, "semantic-result")

    def test_fresh_index_state_skips_rebuild(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            cache_dir = repo / ".gate_rag_cache"
            cache_dir.mkdir()
            state_path = cache_dir / "index_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "commit": "abc123",
                        "provider": "local",
                        "model_id": "fake-local-model",
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(CodebaseRAG, "_current_repo_commit", return_value="abc123"):
                rag = CodebaseRAG(str(repo), provider="local", model_id="fake-local-model")
                with mock.patch.object(rag, "_collection_handle") as collection_handle:
                    rag.build_index()

        self.assertTrue(rag._built)
        collection_handle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
