"""The integrated system: python dbms.py --db DIR [--frames 32] [--file script.sql]"""
import sys
from dbms.shell import main

sys.exit(main(sys.argv[1:]))
