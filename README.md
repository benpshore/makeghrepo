purpose of the project is to bootstrap a new local repository with template configuration and set it up on GitHub. 
the following is a bunch of pseudocode and snippets that are not currently configured as an actual application yet. 
TODOS:
```md
[ ] copier templates for py, rust, swift, docker, shell, js, tart.vm, css, etc.
[ ] uv path completions/cli 
[ ] python orchestration of shell scripts and make/just files 
[ ] creation of .gitignore (don't forget annoying DS_store)
[ ] creation of .github workflows and actions configured
[ ] setup to configure branch protection rules for the target new repo
[ ] setup to configure PR requirement for the target new repo and PR template
[ ] setup to configure issue templates for the target new repo
[ ] copier.yml file config
```
for python:
`uv init "randomword-randomword" -p 3.14 --managed-python`

tmux:
`tmux new-session -A -s main -n randomword-randomword -c "$HOME/code/GitHub/randomword-randomword"`
1. Repository name - must choose
```sh
OWNER=""
REPO="OWNER/REPO"
git init -b main



```


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
