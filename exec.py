"""Wrapper so the spec's command names work:  python exec.py run --db db --plan p.json --frames 32"""
import sys
from execution.cli import main

sys.exit(main(sys.argv[1:]))
