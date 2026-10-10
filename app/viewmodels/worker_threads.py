"""One rule about worker threads, written down once.

Every long job in this app is the same shape: a ``QObject`` worker with a
``run`` slot, moved onto a ``QThread``, reporting back through signals, with the
view model holding a reference to both while they live. The rule that shape has
to obey is:

    **The last Python reference to a worker may only be dropped after its
    thread has finished.**

A worker is created without a Qt parent, so Python owns it and letting the last
reference go *is* a ``delete``. Doing that from the main thread while the
worker's own thread is still alive — and still holding a ``deleteLater`` of its
own — is two threads deleting one object, which Qt ends the process over. It is
a race, so it does not happen every time; it happened often enough in the import
flow to be reported as "cancelling sometimes crashes the app".

The tempting place to release those references is the worker's own ``done``
signal, because that is when the work is over. It is the one place they must not
be released: ``done`` is emitted from inside ``run``, so the thread is by
definition still running. What makes the difference between "usually fine" and
"crashes" is only how much the view model happens to do in between — a handler
that rebuilds a model is often delay enough for the worker to have deleted
itself first, and a cancelled run that reports no result at all has no delay to
offer.

:func:`own_worker` is that rule as four lines of wiring. ``done`` only stops the
thread and asks the worker to delete itself, both of which happen inside the
thread that owns it. The view model's own teardown hangs off ``finished``.

``finished``, however, is *not* the moment the thread is gone, and assuming it
was is what kept the crash alive::

    void QThreadPrivate::finish(...)
    {
        ...
        emit thr->finished(QThread::QPrivateSignal());
        QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);
        ...
    }

Qt emits ``finished`` *before* it flushes the thread's deferred deletes — and
that flush is where the worker's own ``deleteLater`` finally runs. So at the
instant ``finished`` fires, the thread is still running and still owes exactly
the one delete this module exists to serialise against. The view model's
``on_stopped`` is queued to the main thread, so it can drop the last reference —
deleting the worker's C++ object from the *wrong* thread — while the worker
thread is a few instructions away from deleting it itself. Two threads, one
object, again.

That window is small and it is quiet: the main thread only wins the race when it
is free to run the queued slot immediately, which is exactly when nothing is
being drawn. It reads as "the import crashes when you look away".

Hence ``thread.wait()`` on the front of ``finished``. It is a bound method of
the thread object, which lives on the view model's thread, so it is queued like
the two connections after it and runs before them — the thread is genuinely
finished by the time anything else touches either object.

Anything a view model needs to do *promptly* when a job ends — dropping a row's
spinner, announcing a result — still belongs on ``done``. Only the references
have to wait.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QThread


def own_worker(
    thread: QThread,
    worker: QObject,
    on_stopped: Callable[[], None],
) -> None:
    """Wire up who disposes of *worker* and *thread*, and when.

    *on_stopped* must be a bound method of a ``QObject`` living on the same
    thread as *thread* — every call site passes a view model's own slot. That is
    what makes it, and the ``wait`` ahead of it, queued rather than direct; a
    plain function would have no receiver to queue to and would run on the
    worker thread, where waiting on itself is a no-op and the race is back.

    *on_stopped* is called once the thread has genuinely finished, and is where
    the view model releases its references and flips whatever flag says the job
    is over. It takes no arguments: ``QThread.finished`` carries none, and a
    view model that needs to know *which* job ended should keep that on the
    worker's own ``done`` signal, which does.

    Requires *worker* to expose a ``done`` signal emitted exactly once, last,
    on every path out of ``run`` — including the failure and cancellation ones.
    Its arguments, if it has any, are ignored here.
    """
    worker.done.connect(thread.quit)
    worker.done.connect(worker.deleteLater)
    # Must stay first: nothing below may touch the worker or the thread until
    # the thread has finished unwinding. See the module docstring.
    thread.finished.connect(thread.wait)
    thread.finished.connect(on_stopped)
    thread.finished.connect(thread.deleteLater)
