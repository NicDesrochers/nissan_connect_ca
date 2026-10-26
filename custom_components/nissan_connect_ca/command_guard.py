"""Protection contre les commandes dupliquées envoyées au cloud Nissan."""
from __future__ import annotations

import asyncio
import functools
import logging
import time

_LOGGER = logging.getLogger(__name__)

DEFAULT_COOLDOWN = 10.0


def guard_command(cooldown: float = DEFAULT_COOLDOWN):
    """Ignore un appel si le précédent est en cours ou trop récent.

    Évite qu'un double clic ou une automatisation répétée envoie deux fois la
    même commande à distance au véhicule.
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            lock = self.__dict__.setdefault("_command_lock", asyncio.Lock())
            last = self.__dict__.get("_command_last", {})
            key = func.__name__
            now = time.monotonic()
            if lock.locked() or now - last.get(key, -cooldown) < cooldown:
                _LOGGER.debug(
                    "Duplicate command %s ignored for %s", key, getattr(self, "entity_id", self)
                )
                return None
            async with lock:
                try:
                    return await func(self, *args, **kwargs)
                finally:
                    self.__dict__.setdefault("_command_last", {})[key] = time.monotonic()

        return wrapper

    return decorator
