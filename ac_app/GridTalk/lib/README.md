GridTalk bundles the unmodified CPython 3.3 socket extension for AC's 32-bit
and 64-bit Python runtimes. Both copies were taken from the installed acti
Python 3.3 standard library distribution. The Python licence is included.

These modules remove dependence on another AC app adding its own library path
before GridTalk loads. Do not replace them with extensions built for a newer
Python version. tests/ac_runtime.py tests the x64 extension against AC's actual
embedded Python 3.3.5 runtime, without any other app's library paths.
