import os
import subprocess
print(subprocess.run(["python3", "-c", "from tests.test_cli import TestRunGates; t=TestRunGates('test_reports_extension_problem'); t.setUp(); t.test_reports_extension_problem()"], capture_output=True, text=True).stderr)
