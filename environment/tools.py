import os
import subprocess
from pathlib import Path
import json

class CodebaseTools:
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()
        from environment.rag import CodebaseRAG
        self.rag = CodebaseRAG(str(self.repo_path))

    def _resolve_path(self, target_path: str) -> Path:
        target = (self.repo_path / target_path).resolve()
        if self.repo_path not in target.parents and target != self.repo_path:
            raise ValueError(f"Access denied: {target_path} is outside the repository.")
        return target

    def list_directory(self, path: str = ".") -> str:
        try:
            target = self._resolve_path(path)
            if not target.is_dir():
                return f"Error: {path} is not a directory."
            items = os.listdir(target)
            return json.dumps({"directory": path, "items": items})
        except Exception as e:
            return f"Error listing directory: {str(e)}"

    def read_file_content(self, path: str) -> str:
        try:
            target = self._resolve_path(path)
            if not target.is_file():
                return f"Error: {path} is not a file."
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"Error: {path} is a binary or non-UTF-8 file."
        except Exception as e:
            return f"Error reading file: {str(e)}"

    def search_codebase(self, query: str, path: str = ".") -> str:
        try:
            target = self._resolve_path(path)
            result = subprocess.run(
                ["grep", "-rnI", query, str(target)],
                text=True,
                capture_output=True
            )
            out = result.stdout.strip()
            if not out:
                return f"No results found for '{query}' in {path}"
            if len(out) > 5000:
                out = out[:5000] + "\n... [TRUNCATED]"
            return out
        except Exception as e:
            return f"Error searching: {str(e)}"

    def semantic_code_search(self, query: str) -> str:
        """Perform a semantic vector search across the entire codebase."""
        return self.rag.search(query)

    def execute_tool(self, tool_name: str, arguments: dict) -> str:
        if tool_name == "list_directory":
            return self.list_directory(arguments.get("path", "."))
        elif tool_name == "read_file_content":
            return self.read_file_content(arguments.get("path", ""))
        elif tool_name == "search_codebase":
            return self.search_codebase(arguments.get("query", ""), arguments.get("path", "."))
        elif tool_name == "semantic_code_search":
            return self.semantic_code_search(arguments.get("query", ""))
        else:
            return f"Error: Unknown tool '{tool_name}'"

CODEBASE_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List the files and folders inside a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the directory (e.g., '.', 'src/'). Defaults to '.'"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_content",
            "description": "Read the entire content of a specific file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file to read (e.g., 'src/main.py')."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_codebase",
            "description": "Search for a specific string or query across the codebase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The string to search for."},
                    "path": {"type": "string", "description": "Relative path to limit the search (defaults to '.')."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_code_search",
            "description": "Perform a semantic vector search across the entire codebase to find relevant code chunks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "A natural language query (e.g., 'Where is the database connection established?')."}
                },
                "required": ["query"]
            }
        }
    }
]
