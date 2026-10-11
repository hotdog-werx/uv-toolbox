from __future__ import annotations

import typing

from uv_toolbox.lockfile import EnvironmentLock, UvToolboxLock
from uv_toolbox.project import check_environment, lock_environment

if typing.TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Sequence

    from uv_toolbox.settings import UvToolboxEnvironment, UvToolboxSettings


def _lock_entry(  # noqa: PLR0913
    env: UvToolboxEnvironment,
    settings: UvToolboxSettings,
    existing: EnvironmentLock | None,
    *,
    upgrade: bool = False,
    upgrade_packages: Sequence[str] = (),
    refresh: bool = False,
) -> EnvironmentLock:
    """Lock one environment, starting from its existing entry if there is one."""
    uv_lock = lock_environment(
        env,
        settings,
        uv_lock=existing.uv_lock if existing is not None else None,
        legacy_requirements=existing.legacy_requirements if existing is not None else None,
        upgrade=upgrade,
        upgrade_packages=upgrade_packages,
        refresh=refresh,
    )
    return EnvironmentLock(uv_lock=uv_lock, fingerprint=env.lock_fingerprint())


def generate_lock(
    settings: UvToolboxSettings,
    *,
    existing_lock: UvToolboxLock | None = None,
    upgrade: bool = False,
    upgrade_packages: Sequence[str] = (),
    refresh: bool = False,
) -> UvToolboxLock:
    """Lock every configured environment.

    Existing pins are kept wherever they still satisfy the configuration, as
    with `uv lock`; pass upgrade or upgrade_packages to move them.

    Args:
        settings: UV toolbox settings.
        existing_lock: The current repo lockfile contents, if any.
        upgrade: Re-resolve every package to its latest compatible version.
        upgrade_packages: Re-resolve only these packages (and whatever they
            require).
        refresh: Bypass cached package metadata.

    Returns:
        A UvToolboxLock covering exactly the configured environments.
    """
    lock = UvToolboxLock()
    for env in settings.environments:
        existing = existing_lock.environments.get(env.name) if existing_lock is not None else None
        lock.environments[env.name] = _lock_entry(
            env,
            settings,
            existing,
            upgrade=upgrade,
            upgrade_packages=upgrade_packages,
            refresh=refresh,
        )
    return lock


def stale_environments(
    settings: UvToolboxSettings,
    lock: UvToolboxLock,
) -> list[str]:
    """Return names of environments whose lock entry no longer matches the config.

    An entry is stale when it is missing, has no uv.lock content (it was
    written in the version 1 format), or its fingerprint differs from the
    environment's current inputs.

    Args:
        settings: UV toolbox settings.
        lock: The existing repo lockfile contents.

    Returns:
        Stale environment names, in config order.
    """
    stale = []
    for env in settings.environments:
        env_lock = lock.environments.get(env.name)
        if env_lock is None or env_lock.uv_lock is None or env_lock.fingerprint != env.lock_fingerprint():
            stale.append(env.name)
    return stale


def update_lock(
    settings: UvToolboxSettings,
    existing_lock: UvToolboxLock,
    stale: list[str],
) -> UvToolboxLock:
    """Re-lock stale environments, keeping up-to-date entries untouched.

    Stale environments are locked starting from their existing uv.lock, so
    only the packages affected by the config change move. Entries for
    environments no longer in the config are dropped.

    Args:
        settings: UV toolbox settings.
        existing_lock: The existing repo lockfile contents.
        stale: Names of environments to re-lock (see `stale_environments`).

    Returns:
        The updated lock covering exactly the configured environments.
    """
    lock = UvToolboxLock()
    for env in settings.environments:
        existing = existing_lock.environments.get(env.name)
        if existing is not None and env.name not in stale:
            lock.environments[env.name] = existing
            continue
        lock.environments[env.name] = _lock_entry(env, settings, existing)
    return lock


def check_lock(
    settings: UvToolboxSettings,
    lock: UvToolboxLock,
) -> dict[str, str]:
    """Run `uv lock --check` for every environment in an up-to-date lock.

    Args:
        settings: UV toolbox settings.
        lock: The repo lockfile contents; every configured environment must
            have uv.lock content (see `stale_environments`).

    Returns:
        uv's explanation for each environment whose lock is out of date.

    Raises:
        ValueError: If an environment has no uv.lock content.
    """
    failures = {}
    for env in settings.environments:
        uv_lock = lock.environments[env.name].uv_lock
        if uv_lock is None:  # pragma: no cover - callers check staleness first
            msg = f'Environment {env.name!r} has no uv.lock content.'
            raise ValueError(msg)
        problem = check_environment(env, settings, uv_lock=uv_lock)
        if problem is not None:
            failures[env.name] = problem
    return failures
