"""
instead of the following mess even though it's stdlib as written it's not good code yet and gitpython looks like a much cleaner solution. i'll try that instead :)
tbh i just don't feel like writing every command to enumerate with try except blocks so i just got kind of lazy
"""

import subprocess, sys, os
repo_path = os.path.abspath(os.path.dirname(__file__))
command = "uv init $REPO -p 3.14 --managed-python"
try:
    subprocess.run(command, shell=True, check=True)
    subprocess.run(["uv", "init", repo_path], check=True)
    cwd = os.getcwd()
    print(f"Current working directory: {cwd}")
    result = subprocess.run(["uv", "run", "makeghrepo"], capture_output=True, text=True)
    print(result.stdout)
    print(result.stderr)
    text = result.stdout
    check = result.returncode
    if check != 0:
        print(f"Error: {text}")
        sys.exit(1)
except subprocess.CalledProcessError as e:
    print(f"Error: {e}")
    sys.exit(1)

if __name__ == "__main__":
    subprocess.run(["uv", "run", "makeghrepo"])
