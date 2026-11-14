"""Wrapper with the spec name:  python opt.py explain --db db --logical q.json --frames 32"""
import sys
from optimizer.cli import main

sys.exit(main(sys.argv[1:]))
