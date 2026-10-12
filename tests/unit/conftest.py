from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


@pytest.fixture(autouse=True)
def _skip_uv_version_check(mocker: MockerFixture) -> None:
    """Unit tests mock uv invocations, so the installed uv version is never queried."""
    mocker.patch('uv_toolbox.project.ensure_uv_version')
