import subprocess
import os
import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Bash")

@mcp.tool()
def execute_command(command: str, cwd: str = ".") -> str:
    """Run a bash command and return JSON with output and exit_code."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=os.path.abspath(cwd),
            capture_output=True,
            text=True
        )
        out = result.stdout if result.stdout else ""
        err = result.stderr if result.stderr else ""
        full_output = out + "\n" + err
        return json.dumps({"output": full_output.strip(), "exit_code": result.returncode})
    except Exception as e:
        return json.dumps({"output": f"Exception: {str(e)}", "exit_code": 1})

if __name__ == "__main__":
    mcp.run()
