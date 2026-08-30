from keel.swarm import IssueScope, scopes_have_conflict
import time

t0 = time.time()
for _ in range(100):
    scopes_have_conflict(IssueScope(1, predicted_files=("*",)), IssueScope(2, predicted_files=("",)))
    scopes_have_conflict(IssueScope(1, predicted_files=("",)), IssueScope(2, predicted_files=("*",)))

    scopes_have_conflict(IssueScope(1, predicted_files=("*",)), IssueScope(2, predicted_files=("b",)))
    scopes_have_conflict(IssueScope(1, predicted_files=("a",)), IssueScope(2, predicted_files=("*",)))

    scopes_have_conflict(IssueScope(1, predicted_files=("src/keel",)), IssueScope(2, predicted_files=("src/keel/cli.py",)))
    scopes_have_conflict(IssueScope(1, predicted_files=("src/keel/cli.py",)), IssueScope(2, predicted_files=("src/keel",)))

    scopes_have_conflict(IssueScope(1, predicted_files=("src/*.py",)), IssueScope(2, predicted_files=("src/main.py",)))
    scopes_have_conflict(IssueScope(1, predicted_files=("src/main.py",)), IssueScope(2, predicted_files=("src/*.py",)))

    scopes_have_conflict(IssueScope(1, predicted_files=("a",)), IssueScope(2, predicted_files=("a",)))
