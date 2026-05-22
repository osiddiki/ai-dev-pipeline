import docker
import structlog
from pathlib import Path
import os

logger = structlog.get_logger()

class DockerSandbox:
    """Provides an isolated execution environment for the Worker agent.
    
    It mounts the target repository as a volume so the agent can read/write 
    code while being restricted to the container's environment.
    """
    
    def __init__(self, target_repo_path: str, image: str = "python:3.10-slim"):
        self.client = docker.from_env()
        self.target_repo_path = Path(target_repo_path).absolute()
        self.container_workspace = "/workspace"
        self.image = image
        
    def execute_command(self, command: str) -> str:
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
                remove=True,
                detach=False,
                # Security: Limit resources and networking if needed
                mem_limit="512m",
                network_disabled=False # Set to True for pure offline code generation
            )
            return container.decode("utf-8")
        except Exception as e:
            logger.error("Sandbox execution failed", error=str(e))
            return f"Error executing command: {str(e)}"
        
    def run_tests(self) -> str:
        """Run the project's test suite."""
        # This is project specific. For SeviCare Go services, it might be 'go test ./...'
        # For this PoC, we'll try to auto-detect or take it as a param.
        return self.execute_command("ls -R") # Placeholder
        
    def run_linter(self, file_path: str) -> str:
        """Run a linter on a specific file."""
        return self.execute_command(f"python3 -m flake8 {file_path}")
