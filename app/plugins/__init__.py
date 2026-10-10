"""Plugins — everything online, run as separate processes.

The core is a local music player. Whatever reaches the network on its behalf —
web search, matching a file against an online catalogue, fetching audio — comes
from a plugin: a folder with a ``plugin.json``, run in its own process and
spoken to over JSON-RPC on its stdin and stdout. See ``docs/PLUGIN_API.md`` for
the contract and ADR 0015 for why it is a process rather than an import.

The modules, in the order a request travels through them:

- ``manifest``     — what a plugin says it is, checked before anything runs.
- ``paths``        — where installed code, plugin data and temp output live.
- ``installer``    — ``.kpp`` in and out, atomically and zip-safely.
- ``process``      — one running plugin and the JSON-RPC client over its pipes.
- ``host``         — which plugins exist, which are on, starting and stopping.
- ``providers``    — typed façades the rest of the core calls instead of RPC.
- ``external_ids`` — where the ids plugins declare are kept, and the rules for them.
- ``programs``     — finding the programs a plugin needs, and how old they are.
- ``host_entry``   — the interpreter a Python plugin runs under. Imports
  nothing from ``app``.

Nothing outside this package imports ``process`` or speaks the protocol. A view
model or a worker is handed a :class:`~app.plugins.providers.ProviderRegistry`
and asks it for capabilities; whether any plugin supplies them is the registry's
business, and "none does" is an ordinary answer.
"""
