purpose of the project is to bootstrap a new local repository with template configuration and set it up on GitHub. 
the following is a bunch of pseudocode and snippets that are not currently configured as an actual application yet. 

for python:
`uv init "randomword-randomword" -p 3.14 --managed-python`

tmux:
`tmux new-session -A -s main -n randomword-randomword -c "$HOME/code/GitHub/randomword-randomword"`
```sh
gh repo create \
  --public \
  --description "randomword-randomword" \
  --gitignore "Python" \
  --license "MIT" \
  --clone \
  --remote "origin" \
  --push
  ```sh
```sh
gh repo edit \
  --
```
