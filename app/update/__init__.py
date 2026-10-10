"""Noticing a newer release of Kroma Player, and installing it.

A package of pure functions over the network and the filesystem — no library
state, no injected collaborators, nothing to construct — shaped like
:mod:`app.backup` and :mod:`app.gui`. Only the view model is a registry entry,
exactly as :class:`~app.viewmodels.dependencies_view_model.DependenciesViewModel`
is.

Five modules, in the order a check walks through them:

:mod:`~app.update.install_kind`
    How this copy got onto the machine, and therefore whether it may update
    itself at all. The central dispatch: everything else asks this first.
:mod:`~app.update.release`
    What exists upstream. Reads GitHub's releases API and the ``release.json``
    published beside each release's assets.
:mod:`~app.update.download`
    Fetch an asset and verify it against the hash from that manifest, before it
    is run or moved anywhere.
:mod:`~app.update.apply`
    Hand off to the Windows installer, or replace the running AppImage.
:mod:`~app.update.workers`
    The two ``QThread`` workers the view model drives.

**What is not here, and deliberately.** There is no helper process, no
directory swap, no ``.old`` sweep and no rollback branch. Both supported install
kinds solve "a running program cannot replace itself" with a mechanism the
operating system already provides — Windows' Restart Manager on one side, and
the fact that Linux keeps a running executable's inode alive across a rename on
the other. What actually needs protecting is the user's *vault*, not a copy of
the program files, and it has two safety nets. Before every update the updater
advises a backup and offers one (Library → Backup). And whichever build first
opens the vault snapshots its database before migrating it —
:mod:`app.vault.startup`, which also refuses a database a newer build has
migrated. See ADR 0013 and ADR 0014.

**Nothing here downloads anything on its own.** A check reads a few kilobytes of
JSON; every byte after that follows an explicit click.
"""
