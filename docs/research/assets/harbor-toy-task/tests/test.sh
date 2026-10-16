#!/bin/bash
# Harbor uploads tests/ to /tests after the agent finishes, then runs this script in the
# SAME container (verifier environment_mode defaults to "shared"). stdout+stderr go to
# /logs/verifier/test-stdout.txt. The exit code is ignored: only the reward file counts.
python /tests/check.py
