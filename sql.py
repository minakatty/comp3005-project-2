"""Wrapper with the spec name:  python sql.py compile --db db --file q.sql --json"""
import sys
from sqlfront.cli import main

sys.exit(main(sys.argv[1:]))
