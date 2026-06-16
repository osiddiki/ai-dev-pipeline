import sys
import json
import asyncio
from pathlib import Path
from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import structlog

logger = structlog.get_logger()

class PipelineMCPClient:
    """Async MCP client replacing DockerSandbox. Connects to the local Bash MCP server."""
    def __init__(self, target_repo_path: str):
        self.target_repo_path = Path(target_repo_path).resolve()
        self.exit_stack = AsyncExitStack()
        self.bash_session = None

    async def connect(self):
        logger.info("Connecting to Bash MCP server...")
        bash_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_servers.bash"],
            env=None
        )
        transport = await self.exit_stack.enter_async_context(stdio_client(bash_params))
        self.bash_session = await self.exit_stack.enter_async_context(ClientSession(*transport))
        await self.bash_session.initialize()

    async def disconnect(self):
        await self.exit_stack.aclose()

    async def execute_command(self, command: str) -> tuple[str, int]:
        logger.info("Executing via MCP Bash", command=command)
        if not self.bash_session:
            await self.connect()
            
        result = await self.bash_session.call_tool("execute_command", {
            "command": command, 
            "cwd": str(self.target_repo_path)
        })
        
        if not result.content:
            return "", 1
            
        text = result.content[0].text
        try:
            data = json.loads(text)
            return data.get("output", ""), data.get("exit_code", 1)
        except Exception:
            return text, 1
            
    def read_file(self, file_path: str) -> str:
        try:
            return (self.target_repo_path / file_path).read_text(encoding='utf-8')
        except FileNotFoundError:
            return "[File does not exist]"
        except Exception as e:
            return f"[Error] {str(e)}"
