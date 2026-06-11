import os
import sys
import re
import json

def extract_symbols(content, file_extension):
    symbols = []
    if file_extension in ['.ts', '.js', '.tsx', '.jsx']:
        # Match interfaces, classes, and functions/methods
        patterns = [
            r'export\s+(?:interface|type|class|enum)\s+([a-zA-Z0-9_]+)',
            r'class\s+([a-zA-Z0-9_]+)',
            r'(?:export\s+)?function\s+([a-zA-Z0-9_]+)',
            r'(?:public|private|protected|static)?\s*(?:async\s+)?([a-zA-Z0-9_]+)\s*\([^)]*\)\s*[{:]'
        ]
    elif file_extension == '.py':
        # Match classes and functions
        patterns = [
            r'class\s+([a-zA-Z0-9_]+)',
            r'def\s+([a-zA-Z0-9_]+)\s*\('
        ]
    else:
        return []

    for pattern in patterns:
        matches = re.findall(pattern, content)
        symbols.extend(matches)
    
    return sorted(list(set(symbols)))

def build_index(target_repo):
    repo_map = []
    
    for dirpath, _, filenames in os.walk(target_repo):
        if any(ignored in dirpath for ignored in ['.git', 'node_modules', '__pycache__', '.repowise']):
            continue
            
        for filename in filenames:
            ext = os.path.splitext(filename)[1]
            if ext in ['.py', '.ts', '.js', '.tsx']:
                filepath = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(filepath, target_repo)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    symbols = extract_symbols(content, ext)
                    if symbols:
                        repo_map.append(f"{rel_path}: {', '.join(symbols[:20])}") # Limit symbols per file
                except Exception as e:
                    continue
    
    return "\n".join(repo_map[:100]) # Limit total files in map

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python build_repo_index.py <path_to_repo>")
        sys.exit(1)
        
    target_repo = sys.argv[1]
    project_name = os.path.basename(target_repo.rstrip("/"))
    metadata_dir = f"metadata/{project_name}"
    os.makedirs(metadata_dir, exist_ok=True)
    
    print(f"Building index for {target_repo}...")
    index_content = build_index(target_repo)
    
    out_path = os.path.join(metadata_dir, "repo_index.txt")
    with open(out_path, "w") as f:
        f.write(index_content)
        
    print(f"✅ Index built and saved to {out_path}")
