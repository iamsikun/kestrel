#!/usr/bin/env python3
"""Present original specification-kit validation separately from runtime gates."""
import contextlib
import io
import json

import validate_pack

capture = io.StringIO()
with contextlib.redirect_stdout(capture):
    status = validate_pack.main()
if status == 0:
    result = json.loads(capture.getvalue())
    result.pop('framework_implemented', None)
    result['runtime_verification'] = 'not_run; run core, install and applicable isolation suites'
    print(json.dumps(result, indent=2))
raise SystemExit(status)
