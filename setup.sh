#!/bin/bash
# One-shot setup: fixes the venv, installs deps, trains models, runs tests.
set -e
cd "$(dirname "$0")"

rm -rf .venv
if python3 -m venv .venv && [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
  PY=python3
else
  echo ">> venv creation failed, falling back to system python3 (--user installs)"
  rm -rf .venv
  PY=python3
  PIP_USER=--user
fi

$PY -m pip install --upgrade pip $PIP_USER
$PY -m pip install $PIP_USER -r requirements.txt

[ -f data/clips.npz ] || $PY -m ml.data data/raw/WiFall data/clips.npz
$PY -m ml.train_rf data/clips.npz --rate 50
$PY -m ml.train_cnn
$PY -m ml.evaluate
$PY -m pytest -q

[ -d .git ] || { git init -q && git add . && git commit -qm "WiCare initial" && echo ">> git repo created"; }
echo ">> ALL DONE. Next time just run: source .venv/bin/activate"
