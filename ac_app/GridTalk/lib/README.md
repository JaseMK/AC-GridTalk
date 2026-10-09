GridTalk bundles the unmodified CPython 3.3 `_socket` extension for Assetto
Corsa's 32-bit and 64-bit Python runtimes, under the included Python licence.

AC's embedded Python 3.3 doesn't ship `_socket` on its default path. Bundling it
means GridTalk works without relying on another app to add a library path first.
Do not replace these with extensions built for a newer Python version.
