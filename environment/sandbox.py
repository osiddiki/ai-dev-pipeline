import docker
import structlog
from pathlib import Path
import os
import sys

logger = structlog.get_logger()

class DockerSandbox:
    """Provides an isolated execution environment for the Worker agent.
    
    It mounts the target repository as a volume so the agent can read/write 
    code while being restricted to the container's environment.
    """
    
    def __init__(self, target_repo_path: str, image: str = "mcr.microsoft.com/devcontainers/typescript-node:22"):
        try:
            self.client = docker.from_env()
            # Test connection
            self.client.ping()
        except Exception as e:
            logger.error("DOCKER ERROR: Could not connect to Docker daemon. Is Docker Desktop running?", error=str(e))
            print("\n" + "!"*60)
            print("🚨 DOCKER CONNECTION ERROR")
            print("The GATE pipeline requires Docker Desktop to be running.")
            print("If Docker is open, it might be 'stuck' or 'starting'.")
            print("Please restart Docker Desktop and try again.")
            print("!"*60 + "\n")
            sys.exit(1)
        
        self.target_repo_path = Path(target_repo_path).absolute()
        self.container_workspace = "/workspace"
        self.image = image
        
    def execute_command(self, command: str) -> tuple[str, int]:
        """Run a command inside a sandboxed container mounted with the repo."""
        logger.info("Executing command in sandbox", command=command, repo=str(self.target_repo_path))
        
        try:
            # SAFETY: Configure git to trust the workspace. We use a more robust shell string.
            # We check if git exists before trying to configure it to avoid exit code 127/2 noise.
            safe_git_cmd = (
                f"if command -v git >/dev/null 2>&1; then "
                f"git config --global --add safe.directory {self.container_workspace}; "
                f"fi; {command}"
            )
            
            container = self.client.containers.run(
                image=self.image,
                command=["bash", "-c", safe_git_cmd],
                volumes={
                    str(self.target_repo_path): {
                        'bind': self.container_workspace,
                        'mode': 'rw'
                    }
                },
                working_dir=self.container_workspace,
                detach=True, # Detach to wait for result
                mem_limit="512m",
                network_disabled=False
            )
            result = container.wait()
            exit_code = result.get("StatusCode", 0)
            output = container.logs().decode("utf-8")
            container.remove()
            
            if exit_code != 0:
                # EXIT CODE GUIDE:
                # 1: General error or 'False' result (e.g. test -f failed, grep found nothing)
                # 2: File not found or Shell error
                # 127: Command not found (REAL ERROR)
                if exit_code in [1, 2]:
                    logger.info("Sandbox command returned non-zero (often expected)", exit_code=exit_code)
                else:
                    logger.error("Sandbox execution failed", exit_code=exit_code)
                
            return output, exit_code
        except Exception as e:
            logger.error("Sandbox exception", error=str(e))
            return f"Error executing command: {str(e)}", 1
        
    def write_file(self, file_path: str, content: str) -> bool:
        """Write content to a file in the sandbox using base64 for safety."""
        logger.info("Writing file in sandbox", path=file_path)
        import base64
        encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
        # Ensure parent directory exists inside the container before writing
        output, exit_code = self.execute_command(f"mkdir -p $(dirname {file_path}) && echo '{encoded}' | base64 -d > {file_path}")
        return exit_code == 0

    def read_file(self, file_path: str) -> str:
        """Read a file from the sandbox safely."""
        logger.info("Reading file in sandbox", path=file_path)
        # Check if exists first to avoid error log noise
        exists_out, exists_code = self.execute_command(f"[ -f {file_path} ] && echo 'yes' || echo 'no'")
        if exists_out.strip() != "yes":
            return "[File does not exist]"
        output, exit_code = self.execute_command(f"cat {file_path}")
        return output

    def read_file_window(self, file_path: str, start_line: int, end_line: int) -> str:
        """ACI Paginator: Read a specific window of lines from a file."""
        logger.info("Reading file window", path=file_path, start=start_line, end=end_line)
        exists_out, _ = self.execute_command(f"[ -f {file_path} ] && echo 'yes' || echo 'no'")
        if exists_out.strip() != "yes":
            return "[File does not exist]"
        output, _ = self.execute_command(f"sed -n '{start_line},{end_line}p' {file_path}")
        return output

    def check_latex(self, file_path: str) -> str:
        """Check if a LaTeX file is valid by running a non-stop interaction build."""
        # Note: requires texlive or similar in the image
        logger.info("Verifying LaTeX integrity", path=file_path)
        output, exit_code = self.execute_command(f"pdflatex -interaction=nonstopmode -halt-on-error {file_path}")
        return output
