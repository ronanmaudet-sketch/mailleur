Numerical Python prototype tests retired explicitly in rc4
========================================================

The executable Python geometry, ear-clipping, subdivision, smoothing and
training implementations have been removed to meet the requested language
boundary. These files preserve the old test specification as non-executable
historical text, outside unittest discovery. Their retirement is NOT a passing
test, a skipped test or evidence that the old algorithm still exists.

57 previous tests were retired:
  test_geometry.py      7
  test_meshing.py      10
  test_properties.py    9
  test_learning.py     12
  test_io.py            8
  test_advisor.py      11

Replacement checks are in test_python_boundary.py, test_advisor.py and
test_io.py. They verify explicit deprecation, transport to the Rust kernel,
input/output handling, and absence of a Python numerical fallback.

The real Rust kernel integration, mesh quality, critical zones, corpus,
learning, imports, exports and GUI tests remain executable in tests/ and
rust_core/src. New validation counts must be measured; do not compare them to
the former total as if all tests exercised the same software.
