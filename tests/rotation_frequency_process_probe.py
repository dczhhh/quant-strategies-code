"""Compare complete invented frequency report bytes across independent processes."""

import base64
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from tests.strategies.test_rotation_research import inputs, settings
from tests.test_rotation_frequency import intraminute_overweight_inputs
from tools import rotation_frequency_experiment as v


def build():
    scenarios = {}
    for name, source in {
        "bear": inputs(bear=True),
        "drift": intraminute_overweight_inputs(),
    }.items():
        summary, raw = v.matrix(source, primary=False, settings=settings())
        assert all(
            row["status"] == "CONDITIONAL_RESEARCH_PASS" for row in summary["cells"].values()
        ), summary
        scenarios[name] = {"summary": summary, "raw": raw}
    logical = {name: v.u.canonical(scenarios) for name in v.LOGICAL}
    payload = v.pack(logical, "b" * 40)
    return {key: base64.b64encode(body).decode() for key, body in sorted(payload.items())}


if __name__ == "__main__":
    if sys.argv[1] == "single":
        print(v.u.canonical(build()).decode())
    else:

        def run(seed):
            return subprocess.check_output(
                [sys.executable, "-m", "tests.rotation_frequency_process_probe", "single"],
                env=os.environ | {"PYTHONHASHSEED": str(seed)},
            )

        with ThreadPoolExecutor(2) as pool:
            first, second = list(pool.map(run, (1, 33)))
        assert first == second, "Complete invented frequency report bytes differ"
        print("INVENTED_FREQUENCY_COMPLETE_REPORT_BYTES_PASS")
