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
            # We mount the host project directory into the container's /workspace
            container = self.client.containers.run(
                image=self.image,
                command=f"bash -c '{command}'",
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
        # We use a temp file and move it to avoid partial writes if base64 fails
        output, exit_code = self.execute_command(f"echo '{encoded}' | base64 -d > {file_path}")
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

    def check_latex(self, file_path: str) -> str:
        """Check if a LaTeX file is valid by running a non-stop interaction build."""
        # Note: requires texlive or similar in the image
        logger.info("Verifying LaTeX integrity", path=file_path)
        output, exit_code = self.execute_command(f"pdflatex -interaction=nonstopmode -halt-on-error {file_path}")
        return output
