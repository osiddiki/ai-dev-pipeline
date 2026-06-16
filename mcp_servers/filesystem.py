import os
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Filesystem")

@mcp.tool()
def read_file(path: str) -> str:
    """Read a file from the filesystem safely."""
    try:
        target = Path(path).resolve()
        if not target.exists():
            return "[File does not exist]"
        return target.read_text(encoding="utf-8")
    except Exception as e:
        return f"[Error] {str(e)}"

@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write content to a file."""
    try:
        target = Path(path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return "Success"
    except Exception as e:
        return f"[Error] {str(e)}"

@mcp.tool()
def list_directory(path: str = ".") -> str:
    """List contents of a directory."""
    try:
        target = Path(path).resolve()
        return ", ".join(os.listdir(target))
    except Exception as e:
        return f"[Error] {str(e)}"

if __name__ == "__main__":
    mcp.run()
